import hashlib
import json
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
from PyQt6.QtGui import QColor, QImage

from universal_asset_library.library import CancelToken, LibraryRepository
from universal_asset_library.library.polyhaven import PolyHavenClient, PolyHavenError
from universal_asset_library.library.polyhaven_sync import (
    PolyHavenSyncService,
    choose_resolution,
    ownership,
    plan_entry,
)
from universal_asset_library.polyhaven_settings import PolyHavenSyncPreferences


def png():
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor("#718293"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(data)


class Client:
    def __init__(self):
        self.assets = {}
        self.catalogs = {}
        self.payloads = {}
        self.downloads = []
        self.lookups = []
        self.failure = ""

    def record(self, name, payload):
        url = "https://dl.polyhaven.org/" + name
        self.payloads[url] = payload
        return {
            "url": url,
            "size": len(payload),
            "md5": hashlib.md5(payload).hexdigest(),
        }

    def add(self, slug, kind, catalog):
        self.assets[slug] = {
            "type": kind,
            "name": slug.replace("_", " ").title(),
            "date_published": len(self.assets),
            "category": "Wood/Planks",
            "tags": ["warm"],
            "authors": {"Artist": "All"},
            "description": "A test asset.",
            "max_resolution": [4096, 4096],
        }
        self.catalogs[slug] = catalog

    def fetch_assets(self):
        return self.assets

    def fetch_files(self, slug):
        self.lookups.append(slug)
        return self.catalogs[slug]

    def download(self, remote, destination, *, progress=None, cancel=None):
        if cancel:
            cancel()
        if self.failure in remote.url and self.failure:
            raise PolyHavenError("Download failed")
        self.downloads.append(remote.url)
        payload = self.payloads[remote.url]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        if progress:
            progress(len(payload))
        return len(payload), hashlib.sha256(payload).hexdigest()


@pytest.fixture
def client():
    client = Client()
    client.add(
        "wood_test",
        1,
        {"Diffuse": {"4k": {"png": client.record("wood_test_diff_4k.png", png())}}},
    )
    client.add(
        "sky_test",
        0,
        {
            "hdri": {
                "4k": {
                    "hdr": client.record(
                        "sky_test_4k.hdr",
                        b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 2048 +X 4096\nfixture",
                    )
                }
            }
        },
    )
    texture = client.record("ball_diff_4k.png", png())
    include = {"textures/ball_diff_4k.png": texture}
    usd = {
        **client.record("ball_4k.usda", b'#usda 1.0\ndef Xform "Ball" {}\n'),
        "include": include,
    }
    blend = {
        **client.record("ball_4k.blend", b"BLENDER-v400fixture"),
        "include": include,
    }
    client.add(
        "ball",
        2,
        {
            "usd": {"4k": {"usd": usd}},
            "blend": {"4k": {"blend": blend}},
            "Diffuse": {"4k": {"png": texture}},
        },
    )
    return client


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    return LibraryRepository(
        root, render_hdri_previews=False, render_texture_previews=False
    )


def test_all_types_import_repeat_and_model_references(repository, client):
    service = PolyHavenSyncService(repository, client=client)
    review = service.discover(PolyHavenSyncPreferences())
    assert [entry.slug for entry in review.entries] == ["ball", "sky_test", "wood_test"]
    assert review.entries[0].formats == "USD + BLEND"
    assert len(review.entries[0].files) == 3
    result = service.install(review.entries)
    assert not result.failed
    assert len(result.imported) == 3
    assert len(client.downloads) == 5  # Shared model texture transferred only once.
    model = next(asset for asset in result.imported if asset.asset_type == "model")
    assert {item.resolution for item in model.model_files} == {"4K"}
    assert {package.kind for package in model.provider_packages} == {"usd", "blend"}
    for package in model.provider_packages:
        dependency = next(item for item in package.files if item.role == "dependency")
        assert (model.asset_dir / dependency.reference_path).read_bytes() == png()
        assert (
            (model.asset_dir / package.entry_path).parent / "textures/ball_diff_4k.png"
            == model.asset_dir / dependency.reference_path
        )
    for asset in result.imported:
        assert asset.provider == "Poly Haven"
        assert asset.author == "Artist"
        assert asset.description == "A test asset."
        assert "warm" in asset.tags
        assert asset.source_metadata
    client.lookups.clear()
    repeated = service.discover(
        PolyHavenSyncPreferences(hdri_resolution="8K", model_resolution="8K")
    )
    assert not repeated.entries
    assert repeated.owned == 3
    assert not client.lookups
    assert not list((repository.root / ".ual/staging").iterdir())


def test_owned_after_review_skips_download(repository, client):
    service = PolyHavenSyncService(repository, client=client)
    entry = service.discover(PolyHavenSyncPreferences()).entries[-1]
    assert service.install([entry]).imported
    client.downloads.clear()
    assert service.install([entry]).skipped
    assert not client.downloads


def test_asset_imported_during_download_is_rechecked(repository, client):
    service = PolyHavenSyncService(repository, client=client)
    entry = service.discover(PolyHavenSyncPreferences()).entries[-1]
    original = client.download

    def concurrent_import(remote, destination, **kwargs):
        client.download = original
        competing = service.install([entry])
        assert competing.imported
        return original(remote, destination, **kwargs)

    client.download = concurrent_import
    result = service.install([entry])
    assert result.skipped and not result.imported and not result.failed
    assert len(repository.list_assets()) == 1


@pytest.mark.parametrize(
    "requested, expected",
    [("4K", "2K"), ("1K", "2K"), ("8K", "8K"), ("Highest available", "8K")],
)
def test_resolution_fallback(requested, expected):
    assert choose_resolution(["2k", "8k"], requested) == expected


def test_formats_and_single_package(client):
    catalog = {
        "hdri": {
            "4k": {
                "hdr": client.record("a.hdr", b"hdr"),
                "exr": client.record("a.exr", b"exr"),
            }
        }
    }
    entry = plan_entry("a", {}, catalog, "hdri", "4K")
    assert entry.formats == "EXR" and len(entry.files) == 1
    catalog = {
        "Diffuse": {
            "4k": {
                "jpg": client.record("a.jpg", b"jpg"),
                "exr": client.record("a_diff.exr", b"exr"),
            }
        },
        "Rough": {"4k": {"png": client.record("rough.png", b"png")}},
    }
    entry = plan_entry("a", {}, catalog, "texture_set", "4K")
    assert {file.file_format for file in entry.files} == {"exr", "png"}
    entry = plan_entry(
        "ball", {}, {"blend": client.catalogs["ball"]["blend"]}, "model", "4K"
    )
    assert entry.note == "Only BLEND is available."


def test_unsafe_dependency_and_conflicting_packages(client):
    catalog = {
        "usd": {
            "4k": {
                "usd": {
                    **client.record("a.usda", b"usd"),
                    "include": {"../bad.png": client.record("bad.png", b"x")},
                }
            }
        }
    }
    with pytest.raises(PolyHavenError, match="Unsafe"):
        plan_entry("a", {}, catalog, "model", "4K")
    catalog = client.catalogs["ball"]
    catalog["blend"]["4k"]["blend"]["include"] = {
        "textures/ball_diff_4k.png": client.record("different.png", b"other")
    }
    with pytest.raises(PolyHavenError, match="disagree"):
        plan_entry("ball", {}, catalog, "model", "4K")


def test_ownership_metadata_and_ambiguous_names(tmp_path, repository, client):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "info.json").write_text(
        json.dumps(
            {
                "thumbnail_url": "https://cdn.polyhaven.com/asset_img/thumbs/wood_test.png"
            }
        )
    )
    asset = SimpleNamespace(
        asset_type="texture_set",
        name="Wood Test",
        provider="Unknown",
        provider_id="",
        asset_dir=root,
        source_metadata=("info.json",),
    )
    owned, _ = ownership([asset], CancelToken())
    assert ("texture_set", "wood_test") in owned
    asset.source_metadata = ()
    owned, possible = ownership([asset], CancelToken())
    assert not owned and possible[("texture_set", "woodtest")] == ["Wood Test"]
    repository.list_assets = lambda: [asset]
    review = PolyHavenSyncService(repository, client=client).discover(
        PolyHavenSyncPreferences()
    )
    assert next(
        entry for entry in review.entries if entry.slug == "wood_test"
    ).possible_matches == ("Wood Test",)


def test_failure_keeps_success_and_retry(repository, client):
    service = PolyHavenSyncService(repository, client=client)
    review = service.discover(PolyHavenSyncPreferences(models=False))
    client.failure = "wood_test"
    result = service.install(review.entries)
    assert len(result.imported) == 1 and "Wood Test" in result.failed
    client.failure = ""
    retry = service.install(
        service.discover(PolyHavenSyncPreferences(models=False)).entries
    )
    assert len(retry.imported) == 1 and not retry.failed


def test_cancel_removes_partial_download_and_keeps_previous(
    repository, client, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "universal_asset_library.library.polyhaven_sync.tempfile.gettempdir",
        lambda: str(tmp_path),
    )
    token = CancelToken()
    service = PolyHavenSyncService(repository, client=client, token=token)
    entries = service.discover(PolyHavenSyncPreferences(models=False)).entries
    original = client.download

    def cancel_second(remote, destination, **kwargs):
        if "wood_test" in remote.url:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"partial")
            token.cancel()
            token.check()
        return original(remote, destination, **kwargs)

    client.download = cancel_second
    result = service.install(entries)
    assert result.canceled and len(result.imported) == 1 and not result.failed
    assert not list((repository.root / ".ual/staging").iterdir())
    assert not list(tmp_path.glob("shotbox-polyhaven-*"))


def test_insufficient_space(repository, client, monkeypatch):
    service = PolyHavenSyncService(repository, client=client)
    entries = service.discover(PolyHavenSyncPreferences()).entries
    monkeypatch.setattr(
        "universal_asset_library.library.polyhaven_sync.shutil.disk_usage",
        lambda _: SimpleNamespace(free=0),
    )
    result = service.install(entries)
    assert len(result.failed) == 3 and not client.downloads


def test_catalog_refresh_and_rate_limit(monkeypatch):
    from urllib.error import HTTPError
    from io import BytesIO

    calls = []

    class Response(BytesIO):
        def geturl(self):
            return "https://api.polyhaven.com/assets"

    def opening(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 429, "Busy", {"Retry-After": "3"}, None)
        return Response(b'{"a": {"type": 0}}')

    monkeypatch.setattr("universal_asset_library.library.polyhaven.urlopen", opening)
    client = PolyHavenClient()
    delays = []
    monkeypatch.setattr(client, "_pause", lambda delay: delays.append(delay))
    assert client.fetch_assets() == {"a": {"type": 0}}
    assert delays == [3]
    client.fetch_assets()
    assert len(calls) == 3
    assert calls[0].get_header("User-agent").startswith("ShotBoxAssets/")


def test_checksum_failure_and_cancel_clean_part(tmp_path, monkeypatch):
    from io import BytesIO
    from universal_asset_library.library.polyhaven import PolyHavenRemoteFile
    from universal_asset_library.library.repository import ImportCancelled

    url = "https://dl.polyhaven.org/a.exr"

    class Response(BytesIO):
        def geturl(self):
            return url

    client = PolyHavenClient()
    monkeypatch.setattr(client, "_open", lambda _: Response(b"bad"))
    remote = PolyHavenRemoteFile("a.exr", url, 3, hashlib.md5(b"yes").hexdigest())
    target = tmp_path / "a.exr"
    with pytest.raises(PolyHavenError, match="MD5"):
        client.download(remote, target, retries=0)
    assert not list(tmp_path.iterdir())
    token = CancelToken()
    token.cancel()
    with pytest.raises(ImportCancelled):
        client.download(remote, target, cancel=token.check)
    assert not list(tmp_path.iterdir())
