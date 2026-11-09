# Chat answered in Thai: manual verification

Asked "What is the biggest expense I have right now?" in the Plaid view, the chat answered in Thai. It also named the wrong category: Bank Fees ($2,912), when General Merchandise ($3,528.61) was bigger. It gave the same answer every time.

## Cause

Before calling `get_spending`, qwen2.5:14b wrote a short note in Thai saying it would check the last 30 days. The browser and the chat history already drop text written next to a tool call, but the graph still sent that note back to the model with the tool result. The model then kept writing Thai, and its pick of category was off too.

On the real model:

| Change | Language | Biggest expense |
|---|---|---|
| None | Thai | Wrong (Bank Fees) |
| "Always answer in English." added to the system prompt | Thai | Wrong |
| The note left out of what the model is sent | English | Right (General Merchandise) |

## What changed

| File | Change |
|---|---|
| `backend/app/agents/chat_graph.py` | `_without_side_text` blanks the text of any AI message that has tool calls before the history goes to the model. The checkpoint keeps the message as written. Only the model's input changes. |
| `backend/tests/test_chat_graph.py` | `test_text_next_to_a_tool_call_not_sent_back_to_the_model`: the model is sent the call with empty text, and the checkpoint still holds the original. |

This changes the model's input on any turn where it writes a note before a tool call. Benchmark results recorded before this commit came from the old input.

## Check it

1. With the app and Ollama running, switch the dashboard to the Plaid view, open http://localhost:3000/chat and click **New chat**.
2. Ask `What is the biggest expense I have right now?`. The answer is in English, and **Spending** `get_spending` appears in the Tool calls box. Check the category it names against the dashboard's spending breakdown for the last 30 days.
3. Ask `Which debt should I pay first?` and `What did I spend on food last month?`. Both answers are in English.

## A note on the data

On 2026-11-08 the Plaid user had the same sandbox bank linked twice (Plaid items 13 and 15, each with the same seven accounts), so every spending total counts twice. That's a data problem, not this bug. Unlink one of them, or reset the sandbox user, before you compare numbers.

## Tests

- `python -m pytest tests/test_chat_graph.py tests/test_chat_api.py -q` from `backend/`: 77 pass.
- On the real model in the Docker backend after the fix, both questions in step 2 and step 3 came back in English. With the duplicated data, Bank Fees ($5,707.88) really was the biggest, just ahead of General Merchandise ($5,656.45), and the model named it.
