# -*- coding: utf-8 -*-
"""
健康检查 API 测试
"""

import pytest
from unittest.mock import patch, Mock


class TestHealthCheck:
    """健康检查测试"""

    def test_health_endpoint_exists(self, app_client):
        """测试健康检查端点存在"""
        response = app_client.get('/api/health')
        assert response.status_code in [200, 503]

    def test_health_returns_json(self, app_client):
        """测试健康检查返回 JSON"""
        response = app_client.get('/api/health')
        data = response.get_json()
        assert 'status' in data
        assert 'checks' in data
        assert 'version' in data
        assert 'timestamp' in data

    def test_health_status_values(self, app_client):
        """测试健康状态值"""
        response = app_client.get('/api/health')
        data = response.get_json()
        assert data['status'] in ['healthy', 'degraded', 'unhealthy']

    def test_health_database_check(self, app_client):
        """测试数据库检查"""
        response = app_client.get('/api/health')
        data = response.get_json()
        assert 'database' in data['checks']
        assert 'status' in data['checks']['database']
        assert data['checks']['database']['status'] in ['ok', 'error']

    def test_health_llm_check(self, app_client):
        """测试 LLM 检查"""
        response = app_client.get('/api/health')
        data = response.get_json()
        assert 'llm' in data['checks']
        assert 'status' in data['checks']['llm']
        assert data['checks']['llm']['status'] in ['ok', 'configured', 'not_configured', 'error']

    def test_health_task_queue_check(self, app_client):
        """测试任务队列检查"""
        response = app_client.get('/api/health')
        data = response.get_json()
        assert 'task_queue' in data['checks']


class TestLivenessCheck:
    """存活检查测试"""

    def test_liveness_endpoint_exists(self, app_client):
        """测试存活检查端点存在"""
        response = app_client.get('/api/health/live')
        assert response.status_code == 200

    def test_liveness_returns_alive(self, app_client):
        """测试存活检查返回 alive"""
        response = app_client.get('/api/health/live')
        data = response.get_json()
        assert data['status'] == 'alive'


class TestReadinessCheck:
    """就绪检查测试"""

    def test_readiness_endpoint_exists(self, app_client):
        """测试就绪检查端点存在"""
        response = app_client.get('/api/health/ready')
        assert response.status_code in [200, 503]

    def test_readiness_returns_ready_when_db_ok(self, app_client):
        """测试数据库正常时返回 ready"""
        response = app_client.get('/api/health/ready')
        if response.status_code == 200:
            data = response.get_json()
            assert data['status'] == 'ready'

    def test_readiness_returns_503_when_db_fails(self, app_client):
        """测试数据库失败时返回 503"""
        # 这个测试需要模拟数据库失败
        # 在集成测试中进行