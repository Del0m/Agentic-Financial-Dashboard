# qwen2.5_14b-chat-graph vs qwen2.5_14b-chat-none (chat)

| Group | Scenarios | Passed with | Passed without | Wins | Losses | Ties | Verdict |
|---|---|---|---|---|---|---|---|
| all | 34 | 33 | 23 | 11 | 1 | 22 | helps |
| temporal | 15 | 15 | 5 | 10 | 0 | 5 | helps |
| graph_suited | 6 | 6 | 5 | 1 | 0 | 5 | does not help |
| graph_unsuited | 13 | 12 | 13 | 0 | 1 | 12 | does not help |

| Scenario | Group | Result |
|---|---|---|
| kb-roth-vs-401k | graph_suited | win |
| mixed-card-vs-emergency | graph_unsuited | loss |
| temporal-401k-this-year | temporal | win |
| temporal-budget-in-september | temporal | win |
| temporal-budget-since-august | temporal | win |
| temporal-decision-accepted | temporal | win |
| temporal-decision-dismissed | temporal | win |
| temporal-first-shop | temporal | win |
| temporal-gym-start | temporal | win |
| temporal-price-change-month | temporal | win |
| temporal-roth-2024 | temporal | win |
| temporal-subscription-stopped | temporal | win |
