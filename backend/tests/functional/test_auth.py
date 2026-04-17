# -*- coding: utf-8 -*-
"""
Functional tests for authentication (FUNC-01)
"""
import pytest
from models import User, SessionLocal
from utils.security import verify_password


class TestUserRegistration:
    """User registration tests"""

    def test_register_new_user(self, app_client):
        """Test registering a new user"""
        response = app_client.post('/api/register', json={
            'username': 'newuser123',
            'password': 'password123'
        })
        data = response.get_json()
        assert response.status_code == 201
        assert 'message' in data
        assert '注册成功' in data['message']
        assert 'user' in data
        assert 'id' in data['user']

    def test_register_duplicate_user(self, app_client):
        """Test registering with existing username"""
        # First registration
        app_client.post('/api/register', json={
            'username': 'duplicate_user',
            'password': 'password123'
        })
        # Second registration should fail
        response = app_client.post('/api/register', json={
            'username': 'duplicate_user',
            'password': 'password456'
        })
        assert response.status_code == 409
        data = response.get_json()
        assert 'error' in data

    def test_register_empty_username(self, app_client):
        """Test registration with empty username"""
        response = app_client.post('/api/register', json={
            'username': '',
            'password': 'password123'
        })
        assert response.status_code == 400

    def test_register_short_password(self, app_client):
        """Test registration with short password"""
        response = app_client.post('/api/register', json={
            'username': 'validuser',
            'password': '12345'  # Less than 6 chars
        })
        assert response.status_code == 400


class TestUserLogin:
    """User login tests"""

    def test_login_success(self, app_client, test_user):
        """Test successful login"""
        response = app_client.post('/api/login', json={
            'username': test_user['username'],
            'password': test_user['password']
        })
        data = response.get_json()
        assert response.status_code == 200
        assert 'token' in data
        assert 'user' in data

    def test_login_wrong_password(self, app_client, test_user):
        """Test login with wrong password"""
        response = app_client.post('/api/login', json={
            'username': test_user['username'],
            'password': 'wrong_password'
        })
        assert response.status_code == 401

    def test_login_nonexistent_user(self, app_client):
        """Test login with non-existent user"""
        response = app_client.post('/api/login', json={
            'username': 'does_not_exist',
            'password': 'password123'
        })
        assert response.status_code == 401

    def test_login_empty_credentials(self, app_client):
        """Test login with empty credentials"""
        response = app_client.post('/api/login', json={
            'username': '',
            'password': ''
        })
        assert response.status_code == 400


class TestUserInfo:
    """User info tests"""

    def test_get_user_info(self, app_client, auth_token):
        """Test getting current user info"""
        response = app_client.get('/api/user/info', headers={
            'Authorization': f'Bearer {auth_token}'
        })
        data = response.get_json()
        assert response.status_code == 200
        assert 'user' in data
        assert 'username' in data['user']

    def test_get_user_info_no_token(self, app_client):
        """Test getting user info without token"""
        response = app_client.get('/api/user/info')
        assert response.status_code == 401

    def test_get_user_info_invalid_token(self, app_client):
        """Test getting user info with invalid token"""
        response = app_client.get('/api/user/info', headers={
            'Authorization': f'Bearer invalid_token'
        })
        assert response.status_code == 422
