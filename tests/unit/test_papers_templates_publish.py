import pytest
from src.services.data_release.papers_templates_publish import publish_candidate


def test_template_v2_upload_is_rejected_before_remote_access(tmp_path):
    class Client:
        def get_object(self, key):
            raise AssertionError(key)
        def list_objects(self):
            raise AssertionError('list')
        def put_object_conditional(self, *args, **kwargs):
            raise AssertionError('write')

    with pytest.raises(ValueError, match='template v2 publication has been removed'):
        publish_candidate(tmp_path, Client(), workers=2, activate=True)
