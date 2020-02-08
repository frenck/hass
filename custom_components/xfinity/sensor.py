import json
import logging
import requests
import voluptuous as vol

from datetime import timedelta
from re import search

from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    ATTR_ATTRIBUTION,
    CONF_NAME,
    CONF_USERNAME,
    CONF_PASSWORD,
    EVENT_HOMEASSISTANT_START)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity

from .const import *


_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(hours=1)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_USERNAME): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
})


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Setup the sensor platform."""
    name = config.get(CONF_NAME)
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)

    xfinity_data = XfinityUsageData(username, password)
    sensor = XfinityUsageSensor(name, xfinity_data)

    def _first_run():
        sensor.update()
        add_entities([sensor])

    # Wait until start event is sent to load this component.
    hass.bus.listen_once(EVENT_HOMEASSISTANT_START, lambda _: _first_run())


class XfinityUsageSensor(Entity):
    """Representation of the Xfinity Usage sensor."""

    def __init__(self, name, xfinity_data):
        """Initialize the sensor."""
        self._name = name
        self._icon = DEFAULT_ICON
        self._xfinity_data = xfinity_data
        self._state = None

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def icon(self):
        """Return the icon of the sensor."""
        return self._icon

    @property
    def state(self):
        """Return the state of the sensor."""
        if self._xfinity_data.total_usage is not None:
            return self._xfinity_data.total_usage

    @property
    def device_state_attributes(self):
        """Return the state attributes of the last update."""
        if self._xfinity_data.total_usage is None:
            return None

        res = self._xfinity_data.data
        res[ATTR_ATTRIBUTION] = ATTRIBUTION
        return res

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        if self._xfinity_data.unit is not None:
            return self._xfinity_data.unit

    def update(self):
        """Fetch new state data for the sensor."""
        self._xfinity_data.update()


class XfinityUsageData:
    """Xfinity Usage data object"""

    def __init__(self, username, password):
        """Setup usage data object"""
        self.session = requests.Session()
        self.username = username
        self.password = password
        self.raw_data = None
        self.data = None
        self.unit = None
        self.total_usage = None

    def _is_security_check(self, res):
        # does url match?
        if res.url == 'https://idm.xfinity.com/myaccount/security-check?execution=e1s1':
            return True
        # otherwise, try checking response text
        if 'security-check' in res.text:
            return True
        return False

    def update(self):
        """Update usage values"""
        _LOGGER.debug("Finding reqId for login...")
        res = self.session.get('https://customer.xfinity.com/oauth/force_connect/?continue=%23%2Fdevices')
        if res.status_code != 200:
            _LOGGER.error(f"Failed to find reqId, status_code:{res.status_code}")
            return

        m = search(r'<input type="hidden" name="reqId" value="(.*?)">', res.text)
        req_id = m.group(1)
        _LOGGER.debug(f"Found reqId = {req_id}")

        data = {
          'user': self.username,
          'passwd': self.password,
          'reqId': req_id,
          'deviceAuthn': 'false',
          's': 'oauth',
          'forceAuthn': '1',
          'r': 'comcast.net',
          'ipAddrAuthn': 'false',
          'continue': 'https://oauth.xfinity.com/oauth/authorize?client_id=my-account-web&prompt=login&redirect_uri=https%3A%2F%2Fcustomer.xfinity.com%2Foauth%2Fcallback&response_type=code&state=%23%2Fdevices&response=1',
          'passive': 'false',
          'client_id': 'my-account-web',
          'lang': 'en',
        }

        _LOGGER.debug("Posting to login...")
        res = self.session.post('https://login.xfinity.com/login', data=data)
        if res.status_code != 200:
            _LOGGER.error(f"Failed to login, status_code:{res.status_code}")
            _LOGGER.debug(f"Failed response: {res}")
            return
        else:
            _LOGGER.debug(f"Logged in successfully, status_code: {res.status_code}")

        if self._is_security_check(res):
            _LOGGER.warning("Security check found! Please bypass online and try this again.")
            return
            # TODO: figure out 'Ask Me Later' button to bypass Security Check
            # res = self.session.post(res.url, data={'_eventId': 'askMeLater'})
            # _LOGGER.debug(f"{res.status_code, res.reason, res.text}")
            # if res.status_code != 200:
            #     return

        _LOGGER.debug("Fetching internet usage AJAX...")
        res = self.session.get('https://customer.xfinity.com/apis/services/internet/usage')
        if res.status_code != 200:
            _LOGGER.error(f"Failed to fetch data, status_code:{res.status_code}, resp: {res.json()}")
            return

        self.raw_data = json.loads(res.text)
        _LOGGER.debug(f"Received usage data (raw): {self.raw_data}")

        def camelTo_snake_case(str):
            """Converts camelCase strings to snake_case"""
            return ''.join(['_' + i.lower() if i.isupper() else i for i in str]).lstrip('_')

        try:
            _cur_month = self.raw_data['usageMonths'][-1]
            # record current month's information
            # convert key names to 'snake_case'
            self.data = {}
            for k, v in _cur_month.items():
                self.data[camelTo_snake_case(k)] = v

            if _cur_month['policy'] == 'limited':
                # extend data for limited accounts
                self.data['courtesy_used'] = self.raw_data['courtesyUsed']
                self.data['courtesy_remaining'] = self.raw_data['courtesyRemaining']
                self.data['courtesy_allowed'] = self.raw_data['courtesyAllowed']
                self.data['in_paid_overage'] = self.raw_data['inPaidOverage']
                self.data['remaining_usage'] = _cur_month['allowableUsage'] - _cur_month['totalUsage']

            # assign some values as properties
            self.unit = _cur_month['unitOfMeasure']
            self.total_usage = _cur_month['totalUsage']

            # remove 'device' key from data -- perhaps build up MAC list later
            self.data.pop('devices')

        except Exception as e:  # catch all
            _LOGGER.error(f"Something bad happened parsing data from response, err: {e}")

        return
