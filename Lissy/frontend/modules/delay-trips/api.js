/*
 * API functions file
 */

const logService = require('../../../backend/log.js');
const dbPostGIS = require('../../../backend/db-postgis.js');
const dbCache = require('../../../backend/db-cache.js');
const dbStats = require('../../../backend/db-stats.js');
const { riotService, RiotIntegrationError } = require('../../../backend/riot.js');

const env = require('./config.json');

// Help function for log writing
function log(type, msg) {
    logService.write(process.env.FE_MODULE_NAME, type, msg)
}

function mergeObjects(base, extra) {
    return Object.assign(base || {}, extra || {});
}

function intersectNumericSets(sets) {
    if (!Array.isArray(sets) || sets.length < 1) {
        return [];
    }

    let intersection = new Set(sets[0]);
    for (const values of sets.slice(1)) {
        const current = new Set(values || []);
        intersection = new Set([...intersection].filter((item) => current.has(item)));
    }

    return [...intersection].sort((a, b) => a - b);
}

async function getFallbackRouteIds(dates) {
    const intervalSets = [];
    for (const [start, end] of dates) {
        intervalSets.push(await dbStats.getRoutesIdsInInterval(start, end));
    }
    return intersectNumericSets(intervalSets);
}

async function getFallbackTripIds(routeId, dates) {
    const intervalSets = [];
    for (const [start, end] of dates) {
        intervalSets.push(await dbStats.getTripIdsInInterval(routeId, start, end));
    }
    return intersectNumericSets(intervalSets);
}

async function getFallbackTripData(tripId, dates) {
    let result = {};
    for (const [start, end] of dates) {
        result = mergeObjects(result, await dbStats.getTripDataInInterval(tripId, start, end));
    }
    return result;
}

async function tryRiotWithFallback(actionName, riotAction, fallbackAction) {
    try {
        return await riotAction();
    } catch (error) {
        const errorText = error?.message || error;
        log('warning', `RIoT ${actionName} failed, using fallback delay data: ${errorText}`);
        return await fallbackAction();
    }
}

// Main request processing function
async function processRequest(url, req, res) {
    try {
        switch (url[0]) {
            // Returns dates, when shapes are available
            case 'availableDates': {
                res.send(await tryRiotWithFallback(
                    'availableDates',
                    async () => await riotService.getAvailableDates(),
                    async () => await dbStats.getAvailableDates(true)
                ));
                break;
            }
            // Return available routes for selected time interval
            case 'getAvailableRoutes': {
                if (req.query.dates === undefined) {
                    res.send(false);
                } else {
                    let cache = await dbCache.setUpValue(req.url, null, null);

                    if (cache.data !== null) {
                        res.send(cache.data);
                    } else {
                        if (req.query.progress) {
                            res.send({progress: cache.progress});
                        }

                        if (cache.progress > 0 && req.query.progress) {
                            return;
                        }

                        let dates = JSON.parse(req.query.dates);
                        let riotRouteIds = await tryRiotWithFallback(
                            'getAvailableRoutes',
                            async () => await riotService.getAvailableRouteIdsForDates(dates),
                            async () => await getFallbackRouteIds(dates)
                        );
                        let result = [];

                        if (riotRouteIds.length > 0) {
                            const routeDetails = await dbPostGIS.getRoutesDetailByRouteIds(riotRouteIds);
                            const filteredRoutes = [];

                            for (const [idx, route] of routeDetails.entries()) {
                                dbCache.setUpValue(req.url, null, Math.floor((idx / Math.max(routeDetails.length, 1)) * 100));
                                const tripRefs = await tryRiotWithFallback(
                                    `getAvailableTrips(route=${route.route_id})`,
                                    async () => await riotService.getAvailableTripRefsForRoute(dates, route.route_id),
                                    async () => await getFallbackTripIds(route.route_id, dates)
                                );
                                if (tripRefs.length > 0) {
                                    filteredRoutes.push(route);
                                }
                            }

                            result = filteredRoutes;
                        }

                        if (!req.query.progress) {
                            res.send(result);
                        }

                        dbCache.setUpValue(req.url, result, 100);
                    }
                }
                break;
            }
            // Return available trips for selected route and time interval
            case 'getAvailableTrips': {
                const fullStopsOrder = req.query.fullStopOrder === 'true' ? true : false;
                if (req.query.dates === undefined || req.query.route_id === undefined) {
                    res.send(false);
                } else {
                    let dates = JSON.parse(req.query.dates);
                    res.send(await getTripsInInterval(req.query.route_id, dates, fullStopsOrder));
                }
                break;
            }
            // Return full shape with stops and polyline for given shapeId
            case 'getShape': {
                if (req.query.shape_id === undefined) {
                    res.send(false);
                } else {
                    res.send(await dbPostGIS.getFullShape(req.query.shape_id));
                }
                break;
            }
            // Return available trip real operation data for selected tripId and time interval
            case 'getTripData': {
                if (req.query.dates === undefined || req.query.trip_id === undefined) {
                    res.send(false);
                } else {
                    let dates = JSON.parse(req.query.dates);
                    const trip = await dbPostGIS.getTripDetail(parseInt(req.query.trip_id));
                    if (!trip) {
                        res.send({});
                        break;
                    }

                    const fullShape = await dbPostGIS.getFullShape(trip.shape_id);
                    res.send(await tryRiotWithFallback(
                        `getTripData(trip=${trip.trip_id})`,
                        async () => await riotService.getTripKpiData(dates, trip, fullShape),
                        async () => await getFallbackTripData(trip.id, dates)
                    ));
                }
                break;
            }
            default: res.send(false);
        }
    } catch (error) {
        if (error instanceof RiotIntegrationError) {
            log('error', error.message);
        } else {
            log('error', error);
        }

        if (!res.finished) {
            res.send(false);
        }

        if (req.query.progress) {
            dbCache.setUpValue(req.url, false, 100);
        }
    }
}

async function getTripsInInterval(route_id, dates, fullStopsOrder) {
    const route = await dbPostGIS.getRouteDetail(parseInt(route_id));
    if (!route) {
        return [];
    }

    const gtfsTripIds = await tryRiotWithFallback(
        `getTripsInInterval(route=${route.route_id})`,
        async () => await riotService.getAvailableTripRefsForRoute(dates, route.route_id),
        async () => await getFallbackTripIds(route.route_id, dates)
    );
    if (Array.isArray(gtfsTripIds) && gtfsTripIds.length > 0 && typeof gtfsTripIds[0] === 'object') {
        return await dbPostGIS.getTripsDetailByRealtimeRefs(route.route_id, gtfsTripIds, fullStopsOrder);
    }
    return await dbPostGIS.getTripsDetailByTripIds(gtfsTripIds, fullStopsOrder);
}

module.exports = { processRequest, env }
