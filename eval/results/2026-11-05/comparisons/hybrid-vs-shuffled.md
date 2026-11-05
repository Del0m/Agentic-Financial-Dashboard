# qwen2.5_14b-baseline vs qwen2.5_14b-chat-hybrid-shuffled (chat)

| Group | Scenarios | Passed with | Passed without | Wins | Losses | Ties | Verdict |
|---|---|---|---|---|---|---|---|
| all | 34 | 32 | 31 | 2 | 1 | 31 | does not help |
| temporal | 15 | 14 | 12 | 2 | 0 | 13 | helps |
| graph_suited | 6 | 5 | 6 | 0 | 1 | 5 | does not help |
| graph_unsuited | 13 | 13 | 13 | 0 | 0 | 13 | does not help |

| Scenario | Group | Result |
|---|---|---|
| abstain-roth-2030 | graph_suited | loss |
| temporal-401k-2023 | temporal | win |
| temporal-401k-2024 | temporal | win |
