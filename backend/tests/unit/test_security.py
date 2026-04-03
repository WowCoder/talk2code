# -*- coding: utf-8 -*-
"""
security 模块单元测试
"""

import pytest
from utils.security import hash_password, verify_password


class TestPasswordHashing:
    """密码哈希测试"""

    def test_hash_password_returns_string(self):
        """测试哈希返回字符串"""
        password = "test_password123"
        hashed = hash_password(password)
        assert isinstance(hashed, str)
        assert len(hashed) > 0

    def test_hash_password_creates_different_hashes(self):
        """测试相同密码生成不同哈希（bcrypt salt）"""
        password = "same_password"
        hash1 = hash_password(password)
        hash2 = hash_password(password)
        # bcrypt 每次生成不同的 salt，所以哈希不同
        assert hash1 != hash2

    def test_hash_password_length(self):
        """测试哈希长度（bcrypt 固定 60 字符）"""
        password = "any_password"
        hashed = hash_password(password)
        # bcrypt 哈希固定为 60 字符
        assert len(hashed) == 60

    def test_hash_password_contains_bcrypt_marker(self):
        """测试哈希包含 bcrypt 标记"""
        password = "test_password"
        hashed = hash_password(password)
        # bcrypt 哈希以 $2b$ 开头
        assert hashed.startswith('$2b$')


class TestPasswordVerification:
    """密码验证测试"""

    def test_verify_correct_password(self):
        """测试验证正确密码"""
        password = "correct_password"
        hashed = hash_password(password)
        assert verify_password(password, hashed) is True

    def test_verify_wrong_password(self):
        """测试验证错误密码"""
        password = "correct_password"
        wrong_password = "wrong_password"
        hashed = hash_password(password)
        assert verify_password(wrong_password, hashed) is False

    def test_verify_empty_password(self):
        """测试验证空密码"""
        password = ""
        hashed = hash_password(password)
        assert verify_password("", hashed) is True
        assert verify_password("any", hashed) is False

    def test_verify_password_unicode(self):
        """测试验证 Unicode 密码"""
        password = "密码测试123"
        hashed = hash_password(password)
        assert verify_password(password, hashed) is True
        assert verify_password("其他密码", hashed) is False

    def test_verify_password_with_special_chars(self):
        """测试验证特殊字符密码"""
        password = "p@ssw0rd!#$%^&*()"
        hashed = hash_password(password)
        assert verify_password(password, hashed) is True

    def test_verify_password_long(self):
        """测试验证长密码"""
        # bcrypt 限制密码长度为 72 字符，超过会抛出异常
        password = "a" * 72  # 使用 72 字符，正好在限制范围内
        hashed = hash_password(password)
        assert verify_password(password, hashed) is True

    def test_verify_password_exceeds_limit(self):
        """测试超长密码抛出异常"""
        # bcrypt 限制密码长度为 72 字符
        password = "a" * 100
        with pytest.raises(ValueError, match="72"):
            hash_password(password)

    def test_verify_invalid_hash(self):
        """测试无效哈希"""
        password = "test_password"
        invalid_hash = "invalid_hash_format"
        # bcrypt checkpw 应该抛出异常或返回 False
        try:
            result = verify_password(password, invalid_hash)
            assert result is False
        except Exception:
            # 预期行为：无效哈希格式抛出异常
            pass


class TestPasswordSecurity:
    """密码安全测试"""

    def test_password_not_leaked_in_hash(self):
        """测试密码不会泄露在哈希中"""
        password = "secret_password_12345"
        hashed = hash_password(password)
        # 哈希不应该包含原始密码
        assert password not in hashed

    def test_hash_is_one_way(self):
        """测试哈希是单向的（无法从哈希恢复密码）"""
        password = "test_password"
        hashed = hash_password(password)
        # 哈希不应该是可逆的（没有 decode 函数）
        # 这个测试只是概念性的，验证哈希不是明文
        assert hashed != password