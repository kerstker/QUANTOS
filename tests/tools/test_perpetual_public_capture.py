import pytest
from tools.perpetual_public_capture import _NoRedirect,capture_public


@pytest.mark.parametrize('kwargs',[{'samples':True},{'samples':61},{'interval_seconds':0}])
def test_capture_budget_rejects_before_network(kwargs):
    with pytest.raises(ValueError): capture_public(**kwargs)


def test_capture_cannot_follow_redirect_to_other_endpoints():
    with pytest.raises(ValueError,match='redirect'):
        _NoRedirect().redirect_request(None,None,302,'',{},'https://example.com/private')
