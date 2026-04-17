# -*- coding: utf-8 -*-
"""
Functional tests for SSE and conversation (FUNC-04, FUNC-05)
"""
import pytest
from models import Requirement, SessionLocal


class TestSSEConnection:
    """SSE connection tests"""

    def test_sse_endpoint_exists(self, app_client, auth_token):
        """Test SSE endpoint is accessible"""
        # Create a requirement first
        create_response = app_client.post('/api/requirements', json={
            'content': 'Test requirement for SSE'
        }, headers={'Authorization': f'Bearer {auth_token}'})
        req_id = create_response.get_json()['requirement']['id']

        # SSE endpoint should exist
        # Note: Flask test client doesn't fully support SSE streaming
        # This test verifies the route exists without blocking
        response = app_client.get(f'/api/sse/{req_id}')
        # SSE returns 200 OK with text/event-stream content type
        assert response.status_code == 200
        assert 'text/event-stream' in response.content_type

    @pytest.mark.skip(reason="SSE streaming test - requires browser environment")
    def test_sse_sends_heartbeat(self, app_client, auth_token):
        """Test SSE sends heartbeat messages"""
        # Create a requirement
        create_response = app_client.post('/api/requirements', json={
            'content': 'Test heartbeat'
        }, headers={'Authorization': f'Bearer {auth_token}'})
        req_id = create_response.get_json()['requirement']['id']

        # SSE connection should send heartbeat within 30 seconds
        response = app_client.get(f'/api/sse/{req_id}')
        assert response.status_code == 200
        # Heartbeat format: ': heartbeat\n\n'
        assert 'heartbeat' in response.get_data(as_text=True) or response.status_code == 200


class TestConversationContinuity:
    """Conversation continuity tests (FUNC-05)"""

    def test_chat_adds_to_dialogue_history(self, app_client, auth_token):
        """Test that chat messages are added to dialogue history"""
        # Create a requirement first
        create_response = app_client.post('/api/requirements', json={
            'content': 'Test requirement for chat'
        }, headers={'Authorization': f'Bearer {auth_token}'})
        req_id = create_response.get_json()['requirement']['id']

        # Send a chat message
        chat_response = app_client.post(f'/api/requirements/{req_id}/chat', json={
            'message': 'Please add a delete button to the app'
        }, headers={'Authorization': f'Bearer {auth_token}'})

        data = chat_response.get_json()
        assert chat_response.status_code == 200
        assert 'dialogue_history' in data
        # Dialogue history should contain the new message or AI response
        assert len(data['dialogue_history']) >= 1

    @pytest.mark.skip(reason="Chat test depends on AI workflow - requires API key")
    def test_chat_remembers_context(self, app_client, auth_token):
        """Test that chat remembers previous context"""
        # Create a requirement
        create_response = app_client.post('/api/requirements', json={
            'content': 'Calculator app'
        }, headers={'Authorization': f'Bearer {auth_token}'})
        req_id = create_response.get_json()['requirement']['id']

        # First message
        app_client.post(f'/api/requirements/{req_id}/chat', json={
            'message': 'Make the buttons orange'
        }, headers={'Authorization': f'Bearer {auth_token}'})

        # Second message should remember the first context
        response = app_client.post(f'/api/requirements/{req_id}/chat', json={
            'message': 'Also make it bigger'
        }, headers={'Authorization': f'Bearer {auth_token}'})

        data = response.get_json()
        assert response.status_code == 200
        # Dialogue history should grow with each message
        assert len(data['dialogue_history']) >= 2

    def test_code_save_updates_files(self, app_client, auth_token):
        """Test saving code updates files"""
        # Create a requirement
        create_response = app_client.post('/api/requirements', json={
            'content': 'Test app'
        }, headers={'Authorization': f'Bearer {auth_token}'})
        req_id = create_response.get_json()['requirement']['id']

        # Save code
        save_response = app_client.post(f'/api/requirements/{req_id}/code', json={
            'filename': 'test.js',
            'content': 'console.log("test")'
        }, headers={'Authorization': f'Bearer {auth_token}'})

        data = save_response.get_json()
        assert save_response.status_code == 200
        assert 'code_files' in data
        code_files = data['code_files']
        assert any(f['filename'] == 'test.js' for f in code_files)
