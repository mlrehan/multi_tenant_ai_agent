from datetime import UTC, datetime, timedelta

from iam_platform.domain.identity.policies import (
    compute_lockout_expiry,
    is_locked,
    should_lock_account,
    validate_password,
)


class TestValidatePassword:
    def test_accepts_a_strong_password(self) -> None:
        assert validate_password("Correct-Horse9", min_length=12, max_length=256) == []

    def test_rejects_too_short(self) -> None:
        violations = validate_password("Ab1!", min_length=12, max_length=256)
        assert any(v.code == "too_short" for v in violations)

    def test_rejects_too_long(self) -> None:
        violations = validate_password("Ab1!" * 100, min_length=12, max_length=256)
        assert any(v.code == "too_long" for v in violations)

    def test_rejects_low_character_diversity(self) -> None:
        violations = validate_password("alllowercaseletters", min_length=12, max_length=256)
        assert any(v.code == "too_weak" for v in violations)

    def test_accepts_three_of_four_character_classes(self) -> None:
        # lower + upper + digit, no symbol -- still 3 classes, should pass
        assert validate_password("LowerUpper123", min_length=12, max_length=256) == []


class TestLockoutPolicy:
    def test_should_lock_at_threshold(self) -> None:
        assert should_lock_account(failed_attempt_count=5, max_failed_attempts=5) is True

    def test_should_not_lock_below_threshold(self) -> None:
        assert should_lock_account(failed_attempt_count=4, max_failed_attempts=5) is False

    def test_compute_lockout_expiry(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        assert compute_lockout_expiry(now=now, lockout_minutes=15) == now + timedelta(minutes=15)

    def test_is_locked_true_before_unlock_time(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        unlock_at = now + timedelta(minutes=5)
        assert is_locked(unlock_at=unlock_at, now=now) is True

    def test_is_locked_false_after_unlock_time(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        unlock_at = now - timedelta(minutes=1)
        assert is_locked(unlock_at=unlock_at, now=now) is False

    def test_is_locked_false_when_no_lockout(self) -> None:
        assert is_locked(unlock_at=None, now=datetime.now(UTC)) is False
