import sys
import logging
import async_timeout
import voluptuous as vol

from homeassistant.components.switch import PLATFORM_SCHEMA
from homeassistant.const import __version__
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.entity import Entity
import homeassistant.helpers.config_validation as cv

CONF_ID = "id"

_LOGGER = logging.getLogger(__name__)

available_airports = ["BOS", "LGA", "TEB", "EWR", "JFK", "PHL", "PIT", "IAD", "BWI", "DCA", "RDU", "CLT", "ATL", "MCO", "TPA", "MCO", "FLL", "MIA", "DTW", "CLE", "MDW", "ORD", "IND", "CVG", "BNA", "MEM", "STL", "MCI", "MSP", "DFW", "IAH", "DEN", "SLC", "PHX", "LAS", "SAN", "LAX", "SJC", "SFO", "PDX", "SEA"]

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {vol.Required(CONF_ID): vol.In(available_airports)}
)

URL = "https://soa.smext.faa.gov/asws/api/airport/status/{}"

HEADERS = {
    "accept": "application/ld+json",
    "user-agent": f"HomeAssistant/{__version__}",
}

async def async_setup_platform(
    hass, config, add_entities, discovery_info=None
):  # pylint: disable=missing-docstring, unused-argument
    airport = config[CONF_ID]

    session = async_create_clientsession(hass)

    try:
        async with async_timeout.timeout(10, loop=hass.loop):
            response = await session.get(URL.format(airport))
            data = await response.json()

            if "status" in data:
                if data["status"] == 404:
                    _LOGGER.critical("Airport '%s' is not valid", airport)
                    return False

            _LOGGER.debug(data)
            name = data["Name"]

    except Exception as exception:  # pylint: disable=broad-except
        _LOGGER.error("[%s] %s", sys.exc_info()[0].__name__, exception)
        return False

    add_entities([FAAStatusSensor(name, airport, session)], True)
    _LOGGER.info("Added sensor with name '%s' for airport '%s'", name, airport)

class FAAStatusSensor(Entity):
    def __init__(self, name, airport, session):
        self._name = name
        self._unique_id = airport + "_status"
        self.airport = airport
        self.session = session
        self._state = 0
        self.connected = True
        self.exception = None
        self._attr = {
            "departure_delay": False,
            "departure_delay_reason": None,
            "arrival_delay": False,
            "arrival_delay_reason": None,
            "ground_delay": False,
            "ground_delay_reason": None,
            "ground_stop": False,
            "ground_stop_reason": None,
            "end_time": None,
            "closed": False,
            "closure_end": None,
            "closure_reason": None
        }

    async def async_update(self):
        delays = []

        try:
            async with async_timeout.timeout(10, loop=self.hass.loop):
                response = await self.session.get(URL.format(self.airport))
                if response.status != 200:
                    self._state = "unavailable"
                    _LOGGER.critical("FAA Status download failure - HTTP status code %s", response.status)
                else:
                    data = await response.json()
                    
                    self._attr["arrival_delay"] = False
                    self._attr["arrival_delay_reason"] = None
                    self._attr["departure_delay"] = False
                    self._attr["departure_delay_reason"] = None
                    self._attr["ground_delay"] = False
                    self._attr["ground_delay_reason"] = None
                    self._attr["ground_stop"] = False
                    self._attr["ground_stop_reason"] = None
                    self._attr["closed"] = False
                    self._attr["closure_reason"] = None
                    self._attr["end_time"] = None
                    self._attr["closure_end"] = None
                    if data.get("DelayCount") is not 0:
                        for delay in data["Status"]:
                            try:
                                if delay["Type"] == "Arrival":
                                    self._attr["arrival_delay"] = True
                                    self._attr["arrival_delay_reason"] = delay["Reason"]
                                elif delay["Type"] == "Departure":
                                    self._attr["departure_delay"] = True
                                    self._attr["departure_delay_reason"] = delay["Reason"]
                                elif delay["Type"] == "Ground Delay":
                                    self._attr["ground_delay"] = True
                                    self._attr["ground_delay_reason"] = delay["Reason"]
                            except KeyError:
                                try:
                                    if delay["EndTime"]:
                                        self._attr["ground_stop"] = True
                                        self._attr["end_time"] = delay["EndTime"]
                                        self._attr["ground_stop_reason"] = delay["Reason"]
                                except KeyError:
                                    if delay["ClosureEnd"]:
                                        self._attr["closed"] = True
                                        self._attr["closure_end"] = delay["ClosureEnd"]
                                        self._attr["closure_reason"] = delay["Reason"]

                    self._state = data.get("DelayCount")

        except Exception:  # pylint: disable=broad-except
            self.exception = sys.exc_info()[0].__name__
            connected = False
        else:
            connected = True
        finally:
            # Handle connection messages here.
            if self.connected:
                if not connected:
                    self._state = "unavailable"
                    _LOGGER.error(
                        "[%s] Could not update the sensor (%s)",
                        self.airport,
                        self.exception,
                    )

            elif not self.connected:
                if connected:
                    _LOGGER.info("[%s] Update of the sensor completed", self.airport)
                else:
                    self._state = "unavailable"
                    _LOGGER.debug(
                        "[%s] Still no update (%s)", self.airport, self.exception
                    )

            self.connected = connected

    @property
    def name(self):
        """Return the name."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID to use for this sensor."""
        return self._unique_id

    @property
    def state(self):
        """Return the state."""
        return self._state

    @property
    def icon(self):
        """Return icon."""
        return "mdi:airplane"

    @property
    def device_state_attributes(self):
        """Return attributes."""
        return self._attr

    @property
    def unit_of_measurement(self):
        """Return the unit_of_measurement."""
        return "Delays"
