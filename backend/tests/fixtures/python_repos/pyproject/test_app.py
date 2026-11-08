from packaging.version import Version
def test_version(): assert Version("2") > Version("1")
