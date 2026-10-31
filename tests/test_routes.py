"""ルート層テスト: トップページと、削除したサンプルのルート."""

from fastapi.testclient import TestClient


class TestIndexPage:
    """トップページ (店舗一覧) のテスト."""

    def test_index_returns_200(self, client: TestClient) -> None:
        """GET / が 200 を返す."""
        response = client.get("/")
        assert response.status_code == 200

    def test_index_returns_html(self, client: TestClient) -> None:
        """GET / が HTML コンテンツを返す."""
        response = client.get("/")
        assert "text/html" in response.headers["content-type"]

    def test_index_shows_store_list_heading(self, client: TestClient) -> None:
        """トップページに見出し「店舗一覧」がある."""
        response = client.get("/")
        assert "店舗一覧" in response.text

    def test_index_shows_empty_state(self, client: TestClient) -> None:
        """店舗が無いことを表示する."""
        response = client.get("/")
        assert "まだ店舗がありません" in response.text


class TestRemovedSampleRoutes:
    """雛形のサンプル (items / auth) が残っていないこと."""

    def test_items_route_returns_404(self, client: TestClient) -> None:
        """GET /items は 404."""
        assert client.get("/items").status_code == 404

    def test_login_route_returns_404(self, client: TestClient) -> None:
        """GET /login は 404."""
        assert client.get("/login").status_code == 404

    def test_register_route_returns_404(self, client: TestClient) -> None:
        """GET /register は 404."""
        assert client.get("/register").status_code == 404

    def test_static_path_is_not_broken(self, client: TestClient) -> None:
        """静的ファイルのパスが 500 にならない."""
        response = client.get("/static/css/style.css")
        assert response.status_code in (200, 404)
