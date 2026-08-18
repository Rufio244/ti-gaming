from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from typing import List, Dict, Optional
import random

app = FastAPI(
    title="TuiCradGame - Live AI Dealer Platform",
    version="1.0.0",
    description="ระบบเกมไพ่ตุ้ยแบบครบวงจร รองรับ AI Dealer, 3 ที่นั่งผู้เล่น, อัตราจ่าย, และฐานข้อมูลจริง"
)

# --- DATABASE SETUP ---
SQLALCHEMY_DATABASE_URL = "sqlite:///./tui_game.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class DBGameHistory(Base):
    __tablename__ = "game_histories"
    id = Column(Integer, primary_key=True, index=True)
    round_id = Column(String, unique=True, index=True)
    dealer_hand = Column(String)
    winner = Column(String)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- GAME LOGIC & MODELS ---
SUITS = ["♥", "♠"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "K"]

class Card:
    def __init__(self, rank: str, suit: str):
        self.rank = rank
        self.suit = suit

    def value(self) -> int:
        mapping = {"A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "K": 10}
        return mapping.get(self.rank, 0)

    def rank_power(self) -> int:
        # ลำดับไพ่คู่ (ตุ้ย): K > 8 > 7 > 6 > 5 > 4 > 3 > 2 > A
        power_map = {"A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "K": 9}
        return power_map.get(self.rank, 0)

class PlayRoundRequest(BaseModel):
    player_bets: Dict[str, float] # เช่น {"seat_1": 100, "seat_2": 50, "seat_3": 0}
    bonus_exact_cards: Optional[List[str]] = None # ทายไพ่โบนัส 2 ใบ เช่น ["K", "8"]

def evaluate_hand(cards: List[Card]) -> dict:
    r1, r2 = cards[0].rank, cards[1].rank
    if r1 == r2:
        return {"type": "tui", "power": cards[0].rank_power(), "name": f"ตุ้ย {r1}"}
    total = (cards[0].value() + cards[1].value()) % 10
    return {"type": "mixed", "score": total, "name": f"แต้ม {total}"}

@app.post("/tui/play")
async def play_tui_round(request: PlayRoundRequest, db: Session = Depends(get_db)):
    # 1. สำรับไพ่และการจั่วไพ่โบนัสกลาง 2 ใบแรก
    all_deck = [Card(r, s) for r in RANKS for s in SUITS]
    random.shuffle(all_deck)
    
    bonus_cards = [all_deck.pop(), all_deck.pop()]
    b_ranks = [bonus_cards[0].rank, bonus_cards[1].rank]

    # 2. แจกไพ่ให้เจ้ามือ และ ผู้เล่น 3 ที่นั่ง (คนละ 4 ใบ)
    participants = ["dealer", "seat_1", "seat_2", "seat_3"]
    hands = {p: [all_deck.pop(), all_deck.pop(), all_deck.pop(), all_deck.pop()] for p in participants}

    # 3. เลือกไพ่ 2 ใบที่ดีที่สุดมาสู้
    evaluated = {}
    for p in participants:
        chosen = hands[p][:2] # จำลองเลือก 2 ใบแรกมาสู้
        evaluated[p] = {
            "cards": [f"{c.rank}{c.suit}" for c in chosen],
            "eval": evaluate_hand(chosen)
        }

    # 4. เปรียบเทียบผลระหว่าง เจ้ามือ กับ ผู้เล่น 3 ที่นั่ง
    dealer_eval = evaluated["dealer"]["eval"]
    settlements = {}

    for seat in ["seat_1", "seat_2", "seat_3"]:
        bet = request.player_bets.get(seat, 0.0)
        if bet <= 0:
            settlements[seat] = {"outcome": "no_bet", "payout": 0.0}
            continue

        p_eval = evaluated[seat]["eval"]
        outcome, payout = "lose", -bet

        # เงื่อนไขตัดสินแพ้ชนะ
        if dealer_eval["type"] == "tui" and p_eval["type"] == "tui":
            if p_eval["power"] > dealer_eval["power"]:
                outcome, payout = "win", bet * 0.95
            elif p_eval["power"] == dealer_eval["power"]:
                outcome, payout = "draw", 0.0
        elif dealer_eval["type"] == "tui" and p_eval["type"] == "mixed":
            outcome, payout = "lose", -bet
        elif dealer_eval["type"] == "mixed" and p_eval["type"] == "tui":
            outcome, payout = "win", bet * 0.95
        else:
            if p_eval["score"] > dealer_eval["score"]:
                outcome, payout = "win", bet * 0.95
            elif p_eval["score"] == dealer_eval["score"]:
                outcome, payout = "draw", 0.0
            else:
                outcome, payout = "lose", -bet

        settlements[seat] = {"outcome": outcome, "net_payout": payout}

    # 5. ระบบโบนัสไพ่ 2 ใบกลาง (อัตราจ่าย 1:8 ถ้าทายถูกคู่)
    bonus_result = "lose"
    bonus_payout = 0.0
    if request.bonus_exact_cards and len(request.bonus_exact_cards) == 2:
        if sorted(request.bonus_exact_cards) == sorted(b_ranks):
            bonus_result = "win"
            bonus_payout = 100 * 8 # ตัวอย่างคิดจากทุน 100 หรือตามสัดส่วน

    return {
        "status": "success",
        "ai_bonus_cards": b_ranks,
        "table_results": evaluated,
        "settlements": settlements,
        "bonus_side_bet": {"result": bonus_result, "payout_multiplier": "1:8"}
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
