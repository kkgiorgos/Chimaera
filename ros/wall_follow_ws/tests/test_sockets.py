import socket
from pathlib import Path
import pytest
from wall_follow_benchmark.sockets import prepare_sockets


@pytest.fixture
def bound_socket(tmp_path):
    path = tmp_path/'transport.sock'
    with socket.socket(socket.AF_UNIX) as endpoint:
        try:
            endpoint.bind(str(path))
        except PermissionError:
            pytest.skip('Unix socket creation requires unsandboxed integration testing')
        yield path, endpoint


def test_live_socket_is_untouched(bound_socket):
    path, endpoint = bound_socket
    with pytest.raises(RuntimeError, match='Another Chimaera session'):
        prepare_sockets([path])
    assert path.exists()


def test_closed_socket_is_removed(bound_socket):
    path, endpoint = bound_socket
    endpoint.close()
    assert prepare_sockets([path]) == [str(path)]
    assert not path.exists()
    assert prepare_sockets([path]) == []


def test_non_socket_and_symlink_are_untouched(tmp_path):
    path = tmp_path/'file'
    path.write_text('retain')
    link = tmp_path/'link'
    link.symlink_to(path)
    for candidate in (path, link):
        with pytest.raises(RuntimeError, match='non-socket'):
            prepare_sockets([candidate])
    assert path.read_text() == 'retain'
    assert link.is_symlink()


def test_foreign_socket_is_untouched(bound_socket, monkeypatch):
    path, endpoint = bound_socket
    endpoint.close()
    monkeypatch.setattr('wall_follow_benchmark.sockets.os.getuid', lambda: path.stat().st_uid + 1)
    with pytest.raises(RuntimeError, match='foreign-owned'):
        prepare_sockets([path])
    assert path.exists()
