from app.credentials_store import fernet_from_secret, materialize_credentials


def test_fernet_roundtrip():
    f = fernet_from_secret("test-secret")
    tok = f.encrypt(b"sk-REAL")
    assert f.decrypt(tok) == b"sk-REAL"


def test_materialize_injects_credential_list():
    cfg = {"model_list": [{"model_name": "x", "litellm_params": {"model": "openai/gpt-4o", "litellm_credential_name": "openai"}}]}
    decrypted = [{"credential_name": "openai", "provider": "openai", "api_key": "sk-REAL"}]
    out = materialize_credentials(cfg, decrypted)
    cl = out["credential_list"]
    assert cl[0]["credential_name"] == "openai"
    assert cl[0]["credential_values"]["api_key"] == "sk-REAL"
    assert cl[0]["credential_info"]["provider"] == "openai"


def test_materialize_empty_removes_credential_list():
    out = materialize_credentials({"credential_list": [{"credential_name": "old"}], "model_list": []}, [])
    assert not out.get("credential_list")
