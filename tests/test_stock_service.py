from litreel.services.stock_images import StockImageService


def test_stock_service_placeholder_generation():
    service = StockImageService(api_key=None)
    results = service.search("laboratory")
    assert len(results) == 4
    assert results[0]["url"].startswith("https://picsum.photos/")
