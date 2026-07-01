"""Tests for IterationBudget thread safety.

The `used` property must acquire the lock before reading `_used` to prevent
data races with concurrent `consume()` / `refund()` calls.
"""
from concurrent.futures import ThreadPoolExecutor



def test_iteration_budget_used_is_thread_safe():
    """Iterating `used` while other threads consume/refund must not crash.

    Before the fix, `used` returned `_used` directly without holding the lock,
    so a concurrent `consume()` could observe a partially-updated value or
    cause the C-level `list.append` to raise a ValueError ("list size changed").
    """
    from run_agent import IterationBudget

    budget = IterationBudget(max_total=1000)
    num_threads = 10
    operations_per_thread = 200

    errors = []

    def worker(consume: bool):
        try:
            for _ in range(operations_per_thread):
                if consume:
                    budget.consume()
                else:
                    budget.refund()
                # Also read `used` to exercise the property
                _ = budget.used
        except Exception as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=num_threads * 2) as executor:
        # Half the threads consume, half refund
        futures = []
        for i in range(num_threads):
            consume = i < num_threads // 2
            futures.append(executor.submit(worker, consume))
            futures.append(executor.submit(worker, consume))

        for f in futures:
            f.result()

    assert not errors, f"Thread safety violation: {errors}"
    # Final value should be within expected bounds
    assert 0 <= budget.used <= budget.max_total


def test_iteration_budget_consume_returns_false_when_exhausted():
    """consume() must return False once the budget is exhausted."""
    from run_agent import IterationBudget

    budget = IterationBudget(max_total=3)
    assert budget.consume() is True
    assert budget.consume() is True
    assert budget.consume() is True
    assert budget.consume() is False


def test_iteration_budget_refund_restores_consume():
    """refund() after consume() must allow one more consume()."""
    from run_agent import IterationBudget

    budget = IterationBudget(max_total=2)
    assert budget.consume() is True
    assert budget.consume() is True
    assert budget.consume() is False  # exhausted
    budget.refund()
    assert budget.consume() is True


def test_iteration_budget_used_reflects_consume_and_refund():
    """used property must accurately reflect consume() and refund() calls."""
    from run_agent import IterationBudget

    budget = IterationBudget(max_total=10)

    assert budget.used == 0
    budget.consume()
    assert budget.used == 1
    budget.consume()
    assert budget.used == 2
    budget.refund()
    assert budget.used == 1
    budget.refund()
    assert budget.used == 0


def test_iteration_budget_remaining():
    """remaining property must equal max_total - used."""
    from run_agent import IterationBudget

    budget = IterationBudget(max_total=5)

    assert budget.remaining == 5
    budget.consume()
    assert budget.remaining == 4
    budget.consume()
    budget.consume()
    assert budget.remaining == 2
    budget.refund()
    assert budget.remaining == 3


def test_iteration_budget_reset_clears_used():
    """reset() must clear the used counter back to zero.

    This is the core fix for issue #42824: when an agent exhausts its
    iteration budget, the counter persists across turns because the gateway
    caches agent instances.  reset() clears the counter so the next turn
    has the full budget available.
    """
    from run_agent import IterationBudget

    budget = IterationBudget(max_total=90)

    # Exhaust the budget
    for _ in range(90):
        budget.consume()
    assert budget.used == 90
    assert budget.remaining == 0

    # Reset should clear the counter
    budget.reset()
    assert budget.used == 0
    assert budget.remaining == 90

    # Should be able to consume again
    assert budget.consume() is True
    assert budget.used == 1


def test_iteration_budget_reset_preserves_max_total():
    """reset() must not change the max_total cap."""
    from run_agent import IterationBudget

    budget = IterationBudget(max_total=42)
    budget.consume()
    budget.consume()
    budget.reset()
    assert budget.max_total == 42
    assert budget.remaining == 42


def test_iteration_budget_reset_is_thread_safe():
    """reset() must be safe to call while other threads consume/refund."""
    from concurrent.futures import ThreadPoolExecutor
    from run_agent import IterationBudget

    budget = IterationBudget(max_total=1000)
    errors = []

    def consumer():
        try:
            for _ in range(200):
                budget.consume()
        except Exception as exc:
            errors.append(exc)

    def resetter():
        try:
            for _ in range(50):
                budget.reset()
        except Exception as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(consumer),
            executor.submit(consumer),
            executor.submit(resetter),
            executor.submit(resetter),
        ]
        for f in futures:
            f.result()

    assert not errors, f"Thread safety violation: {errors}"
    assert 0 <= budget.used <= budget.max_total
