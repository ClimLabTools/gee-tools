from gee_tools.geometry import tile_bbox

def test_tile_bbox_returns_expected_number_of_tiles():
    # Fake bbox as GeoJSON-like dict
    bbox = {
        "type": "Polygon",
        "coordinates": [
            [
                [0.0, 0.0],  # lower left (xmin, ymin)
                [1.0, 0.0],
                [1.0, 1.0],  # upper right (xmax, ymax)
                [0.0, 1.0],
                [0.0, 0.0],
            ]
        ],
    }

    tiles = tile_bbox(bbox, n_rows=2, n_cols=3, as_ee=False)
    assert len(tiles) == 2 * 3  # rows * cols

    (idx, geom) = tiles[0]
    assert isinstance(idx, tuple)
    assert len(idx) == 2
    assert geom["type"] == "Polygon"
    assert len(geom["coordinates"][0]) == 5  # closed ring

