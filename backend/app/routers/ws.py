import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.database import async_session
from app.services.social.shared_wallet_service import SharedWalletService, wallet_manager
from app.utils.rate_limit import check_rate_limit
from app.utils.security import decode_token
from app.utils.validation import clamp_text, parse_positive_amount

router = APIRouter(tags=["WebSocket"])
shared_service = SharedWalletService()

# Ayni IP icin WS baglanti throttling (handshake flood'u engeller)
_WS_CONNECT_LIMIT = 20
_WS_CONNECT_WINDOW = 60


@router.websocket("/ws/shared-wallet/{wallet_id}")
async def shared_wallet_ws(websocket: WebSocket, wallet_id: str):
    # 1) wallet_id formatini ACCEPT'ten ONCE dogrula: gecersiz id ile
    #    handshake tamamlanmaz, kaynak harcamaz ve sizinti olusturmaz.
    try:
        wallet_uuid = UUID(wallet_id)
    except ValueError:
        return  # accept edilmadi -> starlette handshake'i 403 ile reddeder

    # 2) IP bazli connect throttle (HTTPException -> policy close'a cevrilir)
    try:
        await check_rate_limit(
            websocket, "ws-connect", _WS_CONNECT_LIMIT, _WS_CONNECT_WINDOW
        )
    except HTTPException:
        await websocket.close(code=4029)
        return

    await websocket.accept()
    user_id: UUID | None = None
    authenticated = False

    try:
        auth_msg = await websocket.receive_json()
        if auth_msg.get("action") != "auth":
            await websocket.close(code=4001)
            return
        token = auth_msg.get("token", "")
        decoded = decode_token(token)
        if not decoded:
            await websocket.close(code=4001)
            return
        user_id = decoded

        async with async_session() as db:
            if not await shared_service.is_member(db, wallet_uuid, user_id):
                await websocket.close(code=4003)
                return

        authenticated = True
        await websocket.send_json({"type": "auth_ok"})
        await wallet_manager.connect(wallet_id, websocket)

        while True:
            data = await websocket.receive_json()
            action = data.get("action")

            if action == "expense":
                try:
                    amount = parse_positive_amount(data.get("amount"))
                    description = clamp_text(data.get("description"))
                    user_name = clamp_text(data.get("user_name", "User"), max_len=100)
                except ValueError:
                    await websocket.send_json({"type": "error", "message": "invalid_expense"})
                    continue
                async with async_session() as db:
                    await shared_service.add_expense(
                        db, wallet_uuid,
                        amount=amount,
                        description=description,
                        user_name=user_name,
                        user_id=user_id,
                    )
            elif action == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except (json.JSONDecodeError, ValueError):
        # Malformed JSON / beklenmeyen mesaj: baglantiyi temiz kapat;
        # finally blogu her koşulda manager kaydini siler (sizinti yok).
        try:
            await websocket.close(code=4002)
        except Exception:
            pass
    finally:
        if authenticated:
            wallet_manager.disconnect(wallet_id, websocket)
