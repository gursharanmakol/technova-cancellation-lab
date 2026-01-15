# Hints

Read one at a time.

<details>
<summary>Hint 1</summary>

Start from what went wrong for the customer and for Payments. Walk backwards through the trace until what the workflow believed first differs from `world` at the same tick.

</details>

<details>
<summary>Hint 2</summary>

Compare ORD-4517 with ORD-4520. Find the first step where the workflow's decisions match but the Order Service records differ. Ask where the workflow got its answer. `show` displays everything an event recorded.

</details>

<details>
<summary>Hint 3</summary>

Every read records where it came from. Check what that source's records mean and when they are written (README, The system). `git log --stat` shows when that changed.

</details>
