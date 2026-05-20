---
name: brain-intake
description: Text-only Telegram capture for Dennis Brain. Submits plain-language updates to Brain Intake and returns the Brain receipt without external side effects.
---

# Brain Intake Skill

Use this workflow for every Telegram message routed to the Brain agent.

1. Extract the user's raw text exactly as sent.
2. If there is no usable text, call `brain_telegram_send_message` with a short text-only notice.
3. Call `brain_submit_telegram_update` with:
   - `raw_text`
   - `chat_id`
   - `message_id`
   - `message_thread_id` when present
   - `reply_to_message_id` as the inbound message id when available
   - `sender_name`
   - `sender_id`
   - `chat_title`
4. Do not create a second reply after the tool sends the receipt.
5. If the tool reports Brain is unavailable, do not improvise a classification. The tool will send the outage notice.

The receipt is the product. Keep the worker invisible except when Brain is unavailable or input is unsupported.
