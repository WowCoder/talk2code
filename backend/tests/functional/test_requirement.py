# -*- coding: utf-8 -*-
"""
Functional tests for requirement creation and AI workflow (FUNC-02, FUNC-03)
"""
import pytest
from models import Requirement, SessionLocal


class TestCreateRequirement:
    """Requirement creation tests"""

    def test_create_requirement_success(self, app_client, auth_token):
        """Test creating a new requirement"""
        response = app_client.post('/api/requirements', json={
            'content': '创建一个待办事项应用，支持添加、删除、标记完成任务'
        }, headers={'Authorization': f'Bearer {auth_token}'})
        data = response.get_json()
        assert response.status_code == 201
        assert 'message' in data
        assert 'requirement' in data
        assert 'id' in data['requirement']
        # Status should be 'pending' to trigger workflow
        assert data['requirement']['status'] == 'pending'

    def test_create_requirement_empty_content(self, app_client, auth_token):
        """Test creating requirement with empty content"""
        response = app_client.post('/api/requirements', json={
            'content': ''
        }, headers={'Authorization': f'Bearer {auth_token}'})
        assert response.status_code == 400

    def test_create_requirement_no_auth(self, app_client):
        """Test creating requirement without authentication"""
        response = app_client.post('/api/requirements', json={
            'content': 'Test requirement'
        })
        assert response.status_code == 401


class TestListRequirements:
    """Requirement listing tests"""

    def test_list_requirements(self, app_client, auth_token):
        """Test listing user's requirements"""
        response = app_client.get('/api/requirements', headers={
            'Authorization': f'Bearer {auth_token}'
        })
        data = response.get_json()
        assert response.status_code == 200
        assert 'requirements' in data
        assert isinstance(data['requirements'], list)

    def test_list_requirements_no_auth(self, app_client):
        """Test listing requirements without authentication"""
        response = app_client.get('/api/requirements')
        assert response.status_code == 401


class TestGetRequirementDetail:
    """Requirement detail tests"""

    def test_get_requirement_detail(self, app_client, auth_token):
        """Test getting requirement details"""
        # First create a requirement
        create_response = app_client.post('/api/requirements', json={
            'content': 'Test requirement for detail'
        }, headers={'Authorization': f'Bearer {auth_token}'})
        req_id = create_response.get_json()['requirement']['id']

        # Then get its details
        response = app_client.get(f'/api/requirements/{req_id}', headers={
            'Authorization': f'Bearer {auth_token}'
        })
        data = response.get_json()
        assert response.status_code == 200
        assert 'requirement' in data
        assert 'content' in data['requirement']
        assert 'dialogue_history' in data['requirement']
        assert 'code_files' in data['requirement']
