import rosadmin


def test_package_imports_under_new_name():
    # Proves pytest collects this tree and the renamed package imports cleanly.
    assert rosadmin.__name__ == "rosadmin"
