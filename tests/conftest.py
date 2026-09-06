"""所有自动测试离线运行，不能意外使用开发者真实密钥或连接网络。"""

import socket
import pytest


@pytest.fixture(autouse=True)
def offline_tests(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    def reject_network(*args, **kwargs):
        raise AssertionError("自动测试禁止真实网络；请使用明确的测试替身或MockTransport")
    monkeypatch.setattr(socket.socket,"connect",reject_network)
    monkeypatch.setattr(socket.socket,"connect_ex",reject_network)
