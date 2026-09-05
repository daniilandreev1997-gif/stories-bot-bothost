"""RED-тест конкурентного claim (Этап 6): отсутствие дублей при параллельных
циклах мониторинга TikTok.

ФАКТ кода: функция называется claim_tiktok_post (db/tiktok_claims.py), а не
claim_one_post. В tests/test_tiktok_claims.py конкурентный тест двух
ОДНОВРЕМЕННЫХ вызовов отсутствует — пишем здесь.

Гарантия: при конкурентных claim одного (tg_id, post_id) ровно один вызов
получает ok=True, остальные False; пост не может быть доставлен дважды
(claim-инвариант: status='claimed'/'sent'/'partial'/'fallback' отклоняет
повторный захват).
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import db
from db.tiktok_claims import claim_tiktok_post, get_tiktok_claim_status

TG = 8801
POST = "7450001112223334445"


def test_concurrent_claims_only_one_wins(db_isolated):
    """asyncio.gather двух конкурентных claim_tiktok_post -> ровно один ok."""
    loop = asyncio.new_event_loop()
    executor = ThreadPoolExecutor(max_workers=2)
    try:
        # claim — синхронная блокирующая функция (sqlite + DB_LOCK), поэтому
        # конкурентность эмулируется в потоках через run_in_executor.
        results = loop.run_until_complete(
            asyncio.gather(
                loop.run_in_executor(executor, claim_tiktok_post, TG, POST),
                loop.run_in_executor(executor, claim_tiktok_post, TG, POST),
            )
        )
    finally:
        executor.shutdown(wait=False)
        loop.close()

    assert sum(1 for ok in results if ok) == 1, f"ровно один claim должен выиграть: {results}"
    assert any(not ok for ok in results), f"второй claim должен проиграть: {results}"
    # Статус после двух конкурентных вызовов — 'claimed' (инвариант захвата).
    assert get_tiktok_claim_status(TG, POST) == "claimed"


def test_concurrent_claims_delivered_post_not_reclaimed(db_isolated):
    """После доставки (status='sent') конкурентные claim не проходят — пост
    не может быть отправлен повторно (инвариант «максимум один раз»).
    """
    claim_tiktok_post(TG, POST)
    db.mark_tiktok_post_status(TG, POST, "sent")

    loop = asyncio.new_event_loop()
    executor = ThreadPoolExecutor(max_workers=2)
    try:
        results = loop.run_until_complete(
            asyncio.gather(
                loop.run_in_executor(executor, claim_tiktok_post, TG, POST),
                loop.run_in_executor(executor, claim_tiktok_post, TG, POST),
            )
        )
    finally:
        executor.shutdown(wait=False)
        loop.close()

    assert results == [False, False]
    assert get_tiktok_claim_status(TG, POST) == "sent"
    # Ни один из конкурентных вызовов не «сбросил» доставку: пост остаётся
    # в множестве доставленных ровно один раз.
    assert db.get_tiktok_sent_ids(TG) == {POST}
