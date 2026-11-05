# qwen2.5_14b-chat-vector vs qwen2.5_14b-chat-none (chat)

| Group | Scenarios | Passed with | Passed without | Wins | Losses | Ties | Verdict |
|---|---|---|---|---|---|---|---|
| all | 34 | 23 | 23 | 3 | 3 | 28 | does not help |
| temporal | 15 | 5 | 5 | 2 | 2 | 11 | does not help |
| graph_suited | 6 | 5 | 5 | 1 | 1 | 4 | does not help |
| graph_unsuited | 13 | 13 | 13 | 0 | 0 | 13 | does not help |

| Scenario | Group | Result |
|---|---|---|
| abstain-roth-2030 | graph_suited | loss |
| kb-roth-vs-401k | graph_suited | win |
| temporal-401k-2023 | temporal | loss |
| temporal-401k-2024 | temporal | loss |
| temporal-401k-this-year | temporal | win |
| temporal-roth-2024 | temporal | win |
