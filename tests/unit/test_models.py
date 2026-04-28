def test_scaffold_imports_cleanly() -> None:
    import app.main
    import app.backends.base
    import app.backends.dify
    import app.core.dedup
    import app.core.gateway
    import app.core.models
    import app.core.session
    import app.platforms.base
    import app.platforms.feishu
    import app.platforms.qq
    import app.platforms.wechat

    assert app.main.app is not None
