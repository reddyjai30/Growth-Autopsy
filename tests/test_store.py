from growth_autopsy.store import WorkflowStore


def test_failed_webhook_delivery_can_retry(tmp_path) -> None:
    store = WorkflowStore(tmp_path / "state.db")
    store.initialize()

    assert store.begin_webhook_delivery("msg-1", "fathom") is True
    assert store.begin_webhook_delivery("msg-1", "fathom") is False

    store.finish_webhook_delivery("msg-1", success=False, error="temporary")
    assert store.begin_webhook_delivery("msg-1", "fathom") is True

    store.finish_webhook_delivery("msg-1", success=True)
    assert store.begin_webhook_delivery("msg-1", "fathom") is False

