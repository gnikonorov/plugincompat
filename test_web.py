import os
from unittest import mock

import pytest
import requests
from flask import json

import web
from web import app
from web import get_namespace_for_rendering
from web import get_pytest_versions
from web import get_python_versions
from web import PlugsStorage


@pytest.fixture
def storage():
    with PlugsStorage("sqlite:///:memory:") as st:
        yield st


def make_result_payload(**kwparams):
    result = {
        "name": "mylib",
        "version": "1.0",
        "env": "py27",
        "pytest": "2.3",
        "status": "ok",
        "output": "all commands:\nok",
        "description": "a generic library",
    }
    result.update(kwparams)
    return result


# noinspection PyShadowingNames
class TestPlugsStorage:
    """
    Tests for PlugsStorage class
    """

    def test_add_test_result(self, storage):
        """
        :type storage: PlugsStorage
        """
        assert list(storage.get_all_results()) == []

        with pytest.raises(TypeError):
            # missing "env" key
            invalid_result = make_result_payload()
            del invalid_result["env"]
            storage.add_test_result(invalid_result)

        result1 = make_result_payload()
        storage.add_test_result(result1)
        assert storage.get_test_results("mylib", "1.0") == [result1]

        result2 = make_result_payload(env="py33", status="failed")
        storage.add_test_result(result2)
        assert storage.get_test_results("mylib", "1.0") == [result1, result2]

        result3 = make_result_payload(env="py33")
        storage.add_test_result(result3)
        assert storage.get_test_results("mylib", "1.0") == [result1, result3]

        result4 = make_result_payload(version="1.1", output="another output")
        storage.add_test_result(result4)
        assert storage.get_test_results("mylib", "1.0") == [result1, result3]
        assert storage.get_test_results("mylib", "1.1") == [result4]

    def test_invalid_lib(self, storage):
        assert storage.get_test_results("foobar", "1.0") == []

    def test_get_all_results(self, storage):
        assert list(storage.get_all_results()) == []

        result1 = make_result_payload()
        storage.add_test_result(result1)
        assert list(storage.get_all_results()) == [result1]

        result2 = make_result_payload(version="1.1")
        storage.add_test_result(result2)
        assert list(storage.get_all_results()) == [result1, result2]

        result3 = make_result_payload(name="myotherlib")
        storage.add_test_result(result3)
        assert list(storage.get_all_results()) == [result1, result2, result3]

    def test_drop_all(self, storage):
        result1 = make_result_payload()
        result2 = make_result_payload(version="1.1")
        storage.add_test_result(result1)
        storage.add_test_result(result2)
        assert len(storage.get_all_results()) == 2

        storage.drop_all()
        assert len(storage.get_all_results()) == 0


@pytest.fixture(autouse=True)
def force_sqlite(storage, monkeypatch):
    monkeypatch.setattr(web, "_storage", storage)
    return storage


@pytest.fixture
def client():
    result = app.test_client()
    app.testing = True
    return result


# noinspection PyShadowingNames
class TestView:
    """
    Tests web views for plugincompat
    """

    TEST_SECRET = "123456"

    @pytest.fixture(autouse=True)
    def configure_secret(self, monkeypatch):
        monkeypatch.setenv("POST_KEY", self.TEST_SECRET)

    def post_result(self, client, result, secret=None, expected_status=200):
        data = {"secret": secret or self.TEST_SECRET, "results": result}
        response = client.post("/", data=json.dumps(data), content_type="application/json")
        assert response.status_code == expected_status

    def test_singleton_storage(self):
        assert web.get_storage_for_view() is web.get_storage_for_view()

    def test_auth_failure(self, client, storage):
        assert storage.get_all_results() == []
        self.post_result(client, make_result_payload(), secret="invalid", expected_status=401)
        assert storage.get_all_results() == []

    def test_index_post(self, client, storage):
        result1 = make_result_payload()
        self.post_result(client, result1)
        assert storage.get_all_results() == [result1]

        result2 = make_result_payload(env="py33")
        self.post_result(client, result2)
        assert storage.get_all_results() == [result1, result2]

        result3 = make_result_payload(name="myotherlib")
        result4 = make_result_payload(name="myotherlib", env="py33")
        self.post_result(client, [result3, result4])
        assert storage.get_all_results() == [result1, result2, result3, result4]

    def test_index_get_json(self, client, storage):
        self.post_result(client, make_result_payload())
        self.post_result(client, make_result_payload(env="py33"))
        self.post_result(client, make_result_payload(name="myotherlib"))
        self.post_result(client, make_result_payload(name="myotherlib", env="py33"))
        assert len(storage.get_all_results()) == 4

        response = client.get("/?json=1")
        results = json.loads(response.data)["data"]
        assert {x["name"] for x in results} == {"mylib", "myotherlib"}

    def test_get_render_namespace(self):

        with mock.patch("web.get_python_versions") as mock_python_versions, mock.patch(
            "web.get_pytest_versions"
        ) as mock_pytest_versions:
            mock_python_versions.return_value = {"py27", "py33"}
            mock_pytest_versions.return_value = {"2.4", "2.3"}
            # post results; only the latest lib versions should be rendered
            all_results = [
                make_result_payload(),
                make_result_payload(env="py26", status="failed"),
                make_result_payload(env="py32", status="failed"),
                make_result_payload(env="py33", status="failed"),
                make_result_payload(name="myotherlib", version="1.8", pytest="2.4"),
                make_result_payload(env="py33", pytest="2.4"),
                make_result_payload(env="py33", pytest="2.4", version="0.6"),
                make_result_payload(env="py33", pytest="2.4", version="0.7"),
                make_result_payload(env="py33", pytest="2.4", version="0.8"),
                make_result_payload(
                    name="myotherlib",
                    version="2.0",
                    pytest="2.4",
                    description="my other library",
                    output="output for myotherlib-2.0",
                ),
            ]

            bad_result = make_result_payload(name="badlib")
            del bad_result["output"]
            all_results.append(bad_result)

            output_ok = "all commands:\nok"
            lib_data = {
                ("badlib-1.0", "py27", "2.3"): ("ok", "<no output available>", "a generic library"),
                ("mylib-1.0", "py27", "2.3"): ("ok", output_ok, "a generic library"),
                ("mylib-1.0", "py33", "2.3"): ("failed", output_ok, "a generic library"),
                ("mylib-1.0", "py33", "2.4"): ("ok", output_ok, "a generic library"),
                ("myotherlib-2.0", "py27", "2.4"): (
                    "ok",
                    "output for myotherlib-2.0",
                    "my other library",
                ),
            }

            statuses = {k: status for (k, (status, output, desc)) in lib_data.items()}
            outputs = {k: output for (k, (status, output, desc)) in lib_data.items()}
            descriptions = {k[0]: desc for (k, (status, output, desc)) in lib_data.items()}

            assert get_namespace_for_rendering(all_results) == {
                "python_versions": ["py27", "py33"],
                "lib_names": ["badlib-1.0", "mylib-1.0", "myotherlib-2.0"],
                "pytest_versions": ["2.3", "2.4"],
                "latest_pytest_ver": "2.4",
                "statuses": statuses,
                "outputs": outputs,
                "descriptions": descriptions,
            }

    def test_versions(self):
        assert get_python_versions() == {"py36", "py37", "py38"}
        assert get_pytest_versions() == {"6.0.1"}

    def test_get_with_empty_database(self, client, storage):
        assert len(storage.get_all_results()) == 0

        response = client.get("/")
        assert response.data.decode("utf-8") == "Database is empty"

    @pytest.mark.parametrize("lib_version", ["1.0", "1.2", "latest"])
    def test_get_output(self, client, lib_version):
        self.post_result(client, make_result_payload(version="0.9", output="ver 0.9", pytest="2.3"))
        self.post_result(client, make_result_payload(version="1.0", output="ver 1.0", pytest="2.3"))
        self.post_result(client, make_result_payload(version="1.2", output="ver 1.2", pytest="2.3"))

        url = "/output/mylib-{}?py=py27&pytest=2.3".format(lib_version)
        response = client.get(url)

        if lib_version == "latest":
            lib_version = "1.2"
        assert response.data.decode("utf-8") == "ver {}".format(lib_version)
        assert response.content_type == "text/plain"
        assert response.status_code == 200

    @pytest.mark.parametrize("lib_version", ["1.0", "latest"])
    def test_get_output_missing(self, client, storage, lib_version):
        post_data = make_result_payload()
        del post_data["output"]
        storage.add_test_result(post_data)

        response = client.get("/output/mylib-{}?py=py27&pytest=2.3".format(lib_version))
        assert response.data.decode("utf-8") == "<no output available>"
        assert response.content_type == "text/plain"
        assert response.status_code == 404

    @pytest.mark.parametrize("lib_version", ["1.0", "latest"])
    def test_status_image_help(self, client, lib_version):
        response = client.get("/status/mylib-{}".format(lib_version))
        assert "Plugin Status Images" in response.data.decode("utf-8")

    @pytest.mark.parametrize("lib_version", ["1.0", "latest"])
    def test_status_image(self, client, lib_version):
        self.post_result(client, make_result_payload())

        response = client.get("/status/mylib-{}?py=py27&pytest=2.3".format(lib_version))
        assert response.content_type == "image/png"


def _post_dummy_data():
    """
    posts some dummy data on the local server for manual testing.
    """
    results = [
        make_result_payload(pytest="3.1.0", env="py27", name="pytest-xdist", version="1.14"),
        make_result_payload(pytest="3.1.0", env="py36", name="pytest-xdist", version="1.14"),
        make_result_payload(pytest="3.1.0", env="py36", name="pytest-mock", version="1.6.0"),
    ]

    data = {"secret": os.environ["POST_KEY"], "results": results}
    site = os.environ.get("PLUGINCOMPAT_SITE", "http://127.0.0.1:5000")

    response = requests.post(site, json=data)
    for x in results:
        print(x)

    print("posted to", site)
    print("response:", response.status_code)


if __name__ == "__main__":
    _post_dummy_data()
