def test_package_import():
    import task_orchestrator_core

    assert task_orchestrator_core.__version__ == "1.0.0"
