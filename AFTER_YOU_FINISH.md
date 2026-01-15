# What you learned

**What you practiced**

- **Accepted is not done.** A request that a system accepted may still fail. Only the system that owns the record can tell you it finished.
- **A check is only as strong as its evidence.** Before you trust a passing check, ask where its answer came from and whether that source can know the outcome.
- **Start from the first disagreement, not the loudest error.** The alarm you notice first is often downstream of the step that actually went wrong.
- **Backends limit damage. They do not undo it.** A rule that refuses an unsafe action protects the system, but it cannot take back a message already sent.
- **Test the path, not only the result.** A run can end well by luck. A path check catches the case that only fails when timing changes.
- **Speed and correctness can trade off.** A change made to go faster deserves the same review as a change made to fix a bug.

Caches, read models and local records are useful. Use them for display, deduplication and speed. When a decision has consequences, read the system that owns the truth.
