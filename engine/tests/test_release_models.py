from huddle_engine.providers.transcription import release_models


def test_release_models_is_safe_when_nothing_is_loaded():
    release_models()
    release_models()
