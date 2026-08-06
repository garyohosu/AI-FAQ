from aifaq.security import (
    classify_question,
    contains_dangerous_operation,
    contains_pii_like,
    contains_secret_like,
    redact_for_storage,
)


def test_password_question_is_security_sensitive_and_unsafe():
    c = classify_question("社内Wi-Fiのパスワードを忘れました")
    assert c.scope.value == "SECURITY_SENSITIVE"
    assert c.safe_for_external_research is False


def test_generic_windows_question_is_public_and_safe():
    c = classify_question("Windows 11でネットワークアダプターを再起動する方法は？")
    assert c.scope.value == "PUBLIC_GENERAL"
    assert c.safe_for_external_research is True


def test_internal_procedure_question_is_internal():
    c = classify_question("第2工場の検査PCを交換するときの申請先は？")
    assert c.scope.value == "INTERNAL"
    assert c.safe_for_external_research is False


def test_email_triggers_personal_data():
    c = classify_question("taro.yamada@example.co.jp のアカウントを確認してほしい")
    assert c.scope.value == "PERSONAL_DATA"


def test_dangerous_operation_is_security_sensitive():
    assert contains_dangerous_operation("退職者のアカウント削除をお願いします")
    c = classify_question("退職者のアカウント削除をお願いします")
    assert c.scope.value == "SECURITY_SENSITIVE"


def test_contains_secret_like_detects_keywords_and_values():
    assert contains_secret_like("パスワードを教えて")
    assert contains_secret_like("token: sk-abcdefghijklmnopqrstuvwx")
    assert not contains_secret_like("Windowsの再起動方法")


def test_contains_pii_like():
    assert contains_pii_like("連絡先は 03-1234-5678 です")
    assert not contains_pii_like("Windowsの再起動方法")


def test_redact_for_storage_masks_secret_values():
    redacted = redact_for_storage("パスワードは sk-abcdefghijklmnopqrstuvwx です")
    assert "sk-abcdefghijklmnopqrstuvwx" not in redacted
