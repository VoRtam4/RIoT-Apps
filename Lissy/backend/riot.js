const dotenv = require('dotenv');

const logService = require('./log.js');

dotenv.config();

const riotBaseUrl = (process.env.RIOT_BACKEND_URL || 'http://localhost:9090/rest').replace(/\/+$/, '');
const riotApiKey = (process.env.RIOT_API_KEY || '').trim();
const riotMhdSdTypeUid = (process.env.RIOT_MHD_SD_TYPE_UID || 'MHD_TRIP').trim();

const delayThresholds = {
    low: parseFloat(process.env.RIOT_DELAY_THRESHOLD_LOW || '3'),
    medium: parseFloat(process.env.RIOT_DELAY_THRESHOLD_MEDIUM || '5'),
    high: parseFloat(process.env.RIOT_DELAY_THRESHOLD_HIGH || '10')
};

const severityDefinitions = [
    {
        key: 'green',
        label: 'Lissy MHD Delay under 3 minutes',
        userIdentifier: 'lissy_mhd_delay_green',
        representativeDelay: Math.min(1, delayThresholds.low / 2),
        maxExclusive: delayThresholds.low
    },
    {
        key: 'yellow',
        label: 'Lissy MHD Delay 3 to 5 minutes',
        userIdentifier: 'lissy_mhd_delay_yellow',
        representativeDelay: (delayThresholds.low + delayThresholds.medium) / 2,
        minInclusive: delayThresholds.low,
        maxExclusive: delayThresholds.medium
    },
    {
        key: 'red',
        label: 'Lissy MHD Delay 5 to 10 minutes',
        userIdentifier: 'lissy_mhd_delay_red',
        representativeDelay: (delayThresholds.medium + delayThresholds.high) / 2,
        minInclusive: delayThresholds.medium,
        maxExclusive: delayThresholds.high
    },
    {
        key: 'blue',
        label: 'Lissy MHD Delay 10 minutes and more',
        userIdentifier: 'lissy_mhd_delay_blue',
        representativeDelay: delayThresholds.high + 2,
        minInclusive: delayThresholds.high
    }
];

class RiotIntegrationError extends Error {}

const kpiNodeTypes = {
    logicalOperation: 'LogicalOperation',
    numericLTAtom: 'NumericLTAtom',
    numericGEQAtom: 'NumericGEQAtom'
};

function log(type, msg) {
    logService.write(process.env.BE_API_MODULE_NAME || 'be-api', type, msg);
}

function ensureConfigured() {
    if (!riotApiKey) {
        throw new RiotIntegrationError('Missing required environment variable RIOT_API_KEY');
    }
}

function lissyDateToISO(value) {
    const [year, month, day] = value.split('-').map((item) => parseInt(item, 10));
    return `${year.toString().padStart(4, '0')}-${(month + 1).toString().padStart(2, '0')}-${day.toString().padStart(2, '0')}`;
}

function isoDateToLissy(value) {
    const [year, month, day] = value.split('-').map((item) => parseInt(item, 10));
    return `${year}-${month - 1}-${day}`;
}

function compareIsoDates(a, b) {
    if (a === b) {
        return 0;
    }
    return a < b ? -1 : 1;
}

function addIsoDay(value, offset) {
    const date = new Date(`${value}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() + offset);
    return date.toISOString().slice(0, 10);
}

function expandDateRanges(dateRanges) {
    const dates = [];
    for (const [start, end] of dateRanges) {
        let actual = lissyDateToISO(start);
        const final = lissyDateToISO(end);
        while (compareIsoDates(actual, final) <= 0) {
            dates.push(actual);
            actual = addIsoDay(actual, 1);
        }
    }
    return [...new Set(dates)].sort(compareIsoDates);
}

function intersectValueSets(sets) {
    if (sets.length < 1) {
        return [];
    }
    let intersection = new Set(sets[0]);
    for (const values of sets.slice(1)) {
        const current = new Set(values);
        intersection = new Set([...intersection].filter((item) => current.has(item)));
    }
    return [...intersection];
}

function buildRule(tag, value) {
    return {
        type: 'rule',
        rule: {
            tag: tag,
            operator: 'eq',
            value: value
        }
    };
}

function buildAndFilter(nodes) {
    const filteredNodes = nodes.filter((item) => item !== null && item !== undefined);
    if (filteredNodes.length < 1) {
        return null;
    }
    if (filteredNodes.length === 1) {
        return filteredNodes[0];
    }
    return {
        type: 'logical',
        operator: 'and',
        nodes: filteredNodes
    };
}

function buildTripTimeWindow(serviceDateISO, departureTime) {
    const departureDate = new Date(`${serviceDateISO}T${departureTime}Z`);
    const from = new Date(departureDate.getTime() - (10 * 60 * 1000));
    const to = new Date(from.getTime() + (20 * 60 * 60 * 1000));
    return { from: from.toISOString(), to: to.toISOString() };
}

function normalizeDepartureTime(firstStopInfo) {
    if (!firstStopInfo) {
        return null;
    }

    if (typeof firstStopInfo.aT === 'string' && firstStopInfo.aT.length >= 8) {
        return firstStopInfo.aT.slice(0, 8);
    }

    if (typeof firstStopInfo.dT === 'string' && firstStopInfo.dT.length >= 8) {
        return firstStopInfo.dT.slice(0, 8);
    }

    return null;
}

function buildSegmentKey(fromStopId, toStopId) {
    return `${String(fromStopId)}->${String(toStopId)}`;
}

function buildRealtimeTripRefKey(departureTime) {
    if (typeof departureTime !== 'string' || departureTime.length < 1) {
        return null;
    }
    return departureTime;
}

function intersectTripRefSets(sets) {
    if (sets.length < 1) {
        return [];
    }

    let intersection = new Map(
        sets[0]
            .map((item) => [buildRealtimeTripRefKey(item.departure_time), item])
            .filter(([key]) => key !== null)
    );
    for (const values of sets.slice(1)) {
        const current = new Set(
            values
                .map((item) => buildRealtimeTripRefKey(item.departure_time))
                .filter((item) => item !== null)
        );
        intersection = new Map([...intersection.entries()].filter(([key]) => current.has(key)));
    }
    return [...intersection.values()];
}

function getSegmentKeyFromRawPoint(rawPoint) {
    const fromStopId = rawPoint.data?.segment_from_stop_id;
    const toStopId = rawPoint.data?.segment_to_stop_id;
    if (fromStopId === undefined || toStopId === undefined) {
        return null;
    }
    return buildSegmentKey(fromStopId, toStopId);
}

function getWorstActiveSeverity(kpiState, severityByKpiId) {
    let severity = null;
    for (const [kpiId, fulfilled] of Object.entries(kpiState)) {
        if (fulfilled !== true) {
            continue;
        }
        const current = severityByKpiId[kpiId];
        if (current === undefined) {
            continue;
        }
        if (severity === null || current > severity) {
            severity = current;
        }
    }
    return severity;
}

function materializeSegmentValueOrder(stops, segmentValuesByKey) {
    const values = [];
    let previousDefinedValue;

    for (let idx = 0; idx < (stops.length - 1); idx++) {
        const key = buildSegmentKey(stops[idx], stops[idx + 1]);
        const explicitValue = segmentValuesByKey[key];

        if (explicitValue !== undefined) {
            previousDefinedValue = explicitValue;
            values.push(explicitValue);
            continue;
        }

        if (previousDefinedValue !== undefined) {
            values.push(previousDefinedValue);
        } else {
            values.push(0);
        }
    }

    return values;
}

function materializeIndexedSegmentValues(segmentCount, explicitValuesByIndex) {
    const values = [];
    let previousDefinedValue;

    for (let idx = 0; idx < segmentCount; idx++) {
        const explicitValue = explicitValuesByIndex[idx];
        if (explicitValue !== undefined) {
            previousDefinedValue = explicitValue;
            values.push(explicitValue);
            continue;
        }

        if (previousDefinedValue !== undefined) {
            values.push(previousDefinedValue);
        } else {
            values.push(0);
        }
    }

    return values;
}

function buildLegacyTripDataShape(routeParts, segmentValues) {
    const response = {};

    for (const [routePartIdx, routePart] of routeParts.entries()) {
        const delayValue = segmentValues[routePartIdx] ?? 0;
        response[routePartIdx] = {};
        for (let pointIndex = 0; pointIndex < Math.max(routePart.length - 1, 0); pointIndex++) {
            response[routePartIdx][pointIndex] = delayValue;
        }
    }

    return response;
}

function buildSequentialSegmentIndexMap(rawPoints, segmentCount) {
    const mapping = new Map();
    let nextIndex = 0;

    for (const rawPoint of rawPoints) {
        const segmentKey = getSegmentKeyFromRawPoint(rawPoint);
        if (segmentKey === null || mapping.has(segmentKey)) {
            continue;
        }
        if (nextIndex >= segmentCount) {
            break;
        }
        mapping.set(segmentKey, nextIndex);
        nextIndex++;
    }

    return mapping;
}

function parseTimeSeriesResponse(response) {
    if (!response || !Array.isArray(response.data)) {
        return { data: [], hasMoreData: false, nextCursor: null };
    }
    return {
        data: response.data.map((point) => ({
            time: point.time,
            tags: typeof point.tags === 'string' ? JSON.parse(point.tags || '{}') : (point.tags || {}),
            data: typeof point.data === 'string' ? JSON.parse(point.data || '{}') : (point.data || {})
        })),
        hasMoreData: response.hasMoreData === true,
        nextCursor: response.nextCursor || null
    };
}

function extractRealtimeTripRefs(rawPoints) {
    const refs = new Map();
    for (const point of rawPoints) {
        const departureTime = point.tags?.departure_time;
        const fromStopId = point.tags?.from_stop_id;
        const toStopId = point.tags?.to_stop_id;
        const key = buildRealtimeTripRefKey(departureTime);
        if (key === null || refs.has(key)) {
            continue;
        }
        refs.set(key, {
            departure_time: departureTime,
            from_stop_id: String(fromStopId),
            to_stop_id: String(toStopId),
            trip_id: point.tags?.trip_id ? String(point.tags.trip_id) : null
        });
    }
    return [...refs.values()].sort((a, b) => a.departure_time.localeCompare(b.departure_time, 'en'));
}

function buildNumericNode(id, parentNodeID, type, parameter, referenceValue) {
    return {
        id,
        parentNodeID,
        type,
        sdParameterID: parameter.id,
        sdParameterSpecification: parameter.denotation,
        numericReferenceValue: referenceValue
    };
}

function buildSeverityDefinitionInput(sdType, delayParameter, definition) {
    let nodes = [];
    if (definition.minInclusive !== undefined && definition.maxExclusive !== undefined) {
        nodes = [
            {
                id: 1,
                type: kpiNodeTypes.logicalOperation,
                logicalOperationType: 'and'
            },
            buildNumericNode(2, 1, kpiNodeTypes.numericGEQAtom, delayParameter, definition.minInclusive),
            buildNumericNode(3, 1, kpiNodeTypes.numericLTAtom, delayParameter, definition.maxExclusive)
        ];
    } else if (definition.minInclusive !== undefined) {
        nodes = [
            buildNumericNode(1, null, kpiNodeTypes.numericGEQAtom, delayParameter, definition.minInclusive)
        ];
    } else {
        nodes = [
            buildNumericNode(1, null, kpiNodeTypes.numericLTAtom, delayParameter, definition.maxExclusive)
        ];
    }

    return {
        label: definition.label,
        sdTypeID: sdType.id,
        sdTypeUID: sdType.uid,
        userIdentifier: definition.userIdentifier,
        sdInstanceMode: 'all',
        selectedSDInstanceIDs: [],
        nodes
    };
}

function pickExistingSeverityDefinition(definitions, definition) {
    const byUserIdentifier = definitions
        .filter((item) => item.userIdentifier === definition.userIdentifier)
        .sort((left, right) => left.id - right.id);
    if (byUserIdentifier.length > 0) {
        return byUserIdentifier[0];
    }

    const byLabel = definitions
        .filter((item) => item.label === definition.label)
        .sort((left, right) => left.id - right.id);
    if (byLabel.length > 0) {
        return byLabel[0];
    }

    return null;
}

class RiotMhdService {
    constructor() {
        this.sdType = null;
        this.kpis = null;
        this.warmUpPromise = null;
    }

    async warmUp() {
        if (this.warmUpPromise) {
            return this.warmUpPromise;
        }
        this.warmUpPromise = (async () => {
            await this.getSdType();
            await this.ensureDelayKpis();
        })().catch((error) => {
            this.warmUpPromise = null;
            throw error;
        });
        return this.warmUpPromise;
    }

    async request(method, path, body) {
        ensureConfigured();
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json',
                'X-API-Key': riotApiKey
            }
        };

        if (body !== undefined) {
            options.body = JSON.stringify(body);
        }

        const response = await fetch(`${riotBaseUrl}${path}`, options);
        if (!response.ok) {
            const text = await response.text();
            throw new RiotIntegrationError(`RIoT request failed (${method} ${path}): ${text || response.statusText}`);
        }

        if (response.status === 204) {
            return null;
        }

        return await response.json();
    }

    async getSdType() {
        if (this.sdType !== null) {
            return this.sdType;
        }

        const sdTypes = await this.request('GET', '/sd-types');
        const target = Array.isArray(sdTypes) ? sdTypes.find((item) => item.uid === riotMhdSdTypeUid) : null;
        if (!target) {
            throw new RiotIntegrationError(`RIoT SDType '${riotMhdSdTypeUid}' was not found`);
        }

        this.sdType = target;
        return target;
    }

    async ensureDelayKpis() {
        if (this.kpis !== null) {
            return this.kpis;
        }

        const sdType = await this.getSdType();
        const delayParameter = sdType.parameters.find((item) => item.denotation === 'delay');
        if (!delayParameter) {
            throw new RiotIntegrationError("RIoT MHD SDType does not expose parameter 'delay'");
        }

        let definitions = await this.request('GET', `/kpi-definitions/type/${sdType.id}`);
        const resolved = {};

        for (const definition of severityDefinitions) {
            let existing = pickExistingSeverityDefinition(definitions, definition);
            if (!existing) {
                await this.request('POST', '/kpi-definitions', buildSeverityDefinitionInput(sdType, delayParameter, definition));
                definitions = await this.request('GET', `/kpi-definitions/type/${sdType.id}`);
                existing = pickExistingSeverityDefinition(definitions, definition);
            }
            if (!existing) {
                throw new RiotIntegrationError(`Unable to resolve KPI definition '${definition.userIdentifier}'`);
            }
            if (existing.userIdentifier !== definition.userIdentifier) {
                log('info', `Reusing existing KPI definition by label '${definition.label}' (id=${existing.id}) to avoid duplicates`);
            }
            resolved[definition.key] = {
                definition,
                kpi: existing
            };
        }

        this.kpis = resolved;
        return resolved;
    }

    async distinctRawTagValues(tag, filters) {
        const sdType = await this.getSdType();
        const response = await this.request('POST', '/time-series/distinct-tag-values', {
            type: 'raw',
            sdTypeID: sdType.id,
            tag,
            filters: buildAndFilter(filters || [])
        });
        return Array.isArray(response?.values) ? response.values.filter((item) => item !== '') : [];
    }

    async readKpiTimeSeries(filters) {
        const kpis = await this.ensureDelayKpis();
        return await this.readTimeSeries({
            type: 'kpi',
            kpiDefinitionIDs: severityDefinitions.map((item) => kpis[item.key].kpi.id),
            filters
        });
    }

    async readRawTimeSeries(filters, timeWindow) {
        return await this.readTimeSeries({
            type: 'raw',
            filters,
            from: timeWindow?.from,
            to: timeWindow?.to
        });
    }

    async readTimeSeries({ type, filters, kpiDefinitionIDs = null, from = null, to = null }) {
        const sdType = await this.getSdType();
        let cursor = null;
        let output = [];

        while (true) {
            const payload = {
                type,
                sdTypeID: sdType.id,
                batch: 2000,
                sortDesc: false,
                filters: buildAndFilter(filters || []),
                cursor
            };
            if (Array.isArray(kpiDefinitionIDs) && kpiDefinitionIDs.length > 0) {
                payload.kpiDefinitionIDs = kpiDefinitionIDs;
            }
            if (typeof from === 'string' && from.length > 0) {
                payload.from = from;
            }
            if (typeof to === 'string' && to.length > 0) {
                payload.to = to;
            }

            const response = await this.request('POST', '/time-series', payload);
            const parsed = parseTimeSeriesResponse(response);
            output = output.concat(parsed.data);
            if (!parsed.hasMoreData || !parsed.nextCursor) {
                return output;
            }
            cursor = parsed.nextCursor;
        }
    }

    async getAvailableDates() {
        const dates = (await this.distinctRawTagValues('service_date')).sort(compareIsoDates);
        if (dates.length < 1) {
            return {
                start: isoDateToLissy((new Date()).toISOString().slice(0, 10)),
                disabled: [],
                end: isoDateToLissy((new Date()).toISOString().slice(0, 10))
            };
        }

        const disabled = [];
        let actual = dates[0];
        while (compareIsoDates(actual, dates[dates.length - 1]) <= 0) {
            if (!dates.includes(actual)) {
                disabled.push(isoDateToLissy(actual));
            }
            actual = addIsoDay(actual, 1);
        }

        return {
            start: isoDateToLissy(dates[0]),
            disabled,
            end: isoDateToLissy(dates[dates.length - 1])
        };
    }

    async getAvailableRouteIdsForDates(dateRanges) {
        const days = expandDateRanges(dateRanges);
        if (days.length < 1) {
            return [];
        }

        const daySets = [];
        for (const day of days) {
            daySets.push(await this.distinctRawTagValues('route_id', [buildRule('service_date', day)]));
        }

        return intersectValueSets(daySets).sort();
    }

    async getAvailableTripRefsForRoute(dateRanges, routeId) {
        const days = expandDateRanges(dateRanges);
        if (days.length < 1) {
            return [];
        }

        const daySets = [];
        for (const day of days) {
            const rawPoints = await this.readRawTimeSeries([
                buildRule('service_date', day),
                buildRule('route_id', routeId)
            ], {
                from: `${day}T00:00:00.000Z`,
                to: `${addIsoDay(day, 1)}T00:00:00.000Z`
            });
            daySets.push(extractRealtimeTripRefs(rawPoints));
        }

        return intersectTripRefSets(daySets);
    }

    async getTripKpiData(dateRanges, trip, fullShape) {
        const output = {};
        const segmentCount = Array.isArray(fullShape?.coords) ? fullShape.coords.length : 0;
        if (segmentCount < 1 || !trip || !Array.isArray(trip.stops) || trip.stops.length < 2) {
            return output;
        }

        const departureTime = normalizeDepartureTime(trip.stops_info?.[0]);
        if (departureTime === null) {
            return output;
        }

        const kpis = await this.ensureDelayKpis();
        const severityByKpiId = {};
        severityDefinitions.forEach((definition, index) => {
            severityByKpiId[String(kpis[definition.key].kpi.id)] = index;
        });

        for (const day of expandDateRanges(dateRanges)) {
            const timeWindow = buildTripTimeWindow(day, departureTime);
            const filters = [
                buildRule('service_date', day),
                buildRule('departure_time', departureTime)
            ];
            if (trip.route_id) {
                filters.push(buildRule('route_id', String(trip.route_id)));
            }

            const kpiPoints = await this.readTimeSeries({
                type: 'kpi',
                kpiDefinitionIDs: severityDefinitions.map((item) => kpis[item.key].kpi.id),
                filters,
                from: timeWindow.from,
                to: timeWindow.to
            });
            const rawPoints = await this.readRawTimeSeries(filters, timeWindow);

            let segmentSeverity;
            if (rawPoints.length > 0 && kpiPoints.length > 0) {
                segmentSeverity = buildSegmentValuesFromKpiAndRaw(trip.stops, kpiPoints, rawPoints, severityByKpiId);
            } else if (rawPoints.length > 0) {
                segmentSeverity = buildSegmentValuesFromRawOnly(trip.stops, rawPoints);
            } else {
                segmentSeverity = buildSegmentValuesFromKpiOnly(trip.stops, kpiPoints, severityByKpiId);
            }

            const lissyDate = isoDateToLissy(day);
            output[lissyDate] = buildLegacyTripDataShape(fullShape.coords, segmentSeverity);
        }

        return output;
    }
}

function buildSegmentValuesFromKpiAndRaw(stops, kpiPoints, rawPoints, severityByKpiId) {
    const sortedKpiPoints = [...kpiPoints].sort((a, b) => new Date(a.time) - new Date(b.time));
    const sortedRawPoints = [...rawPoints].sort((a, b) => new Date(a.time) - new Date(b.time));
    const segmentCount = Math.max(stops.length - 1, 0);
    const segmentIndexes = buildSequentialSegmentIndexMap(sortedRawPoints, segmentCount);
    const kpiState = {};
    const segmentValues = {};
    let kpiIndex = 0;

    for (const rawPoint of sortedRawPoints) {
        const rawTime = new Date(rawPoint.time).getTime();
        while (kpiIndex < sortedKpiPoints.length && new Date(sortedKpiPoints[kpiIndex].time).getTime() <= rawTime) {
            const point = sortedKpiPoints[kpiIndex];
            const kpiId = String(point.tags?.kpiDefinitionID || point.data?.kpiDefinitionID || '');
            if (kpiId !== '' && severityByKpiId[kpiId] !== undefined) {
                kpiState[kpiId] = point.data?.fulfilled === true;
            }
            kpiIndex++;
        }

        const segmentKey = getSegmentKeyFromRawPoint(rawPoint);
        if (segmentKey === null) {
            continue;
        }
        const segmentIndex = segmentIndexes.get(segmentKey);
        if (segmentIndex === undefined) {
            continue;
        }

        const severity = getWorstActiveSeverity(kpiState, severityByKpiId);
        if (severity === null) {
            continue;
        }

        const delayValue = severityDefinitions[severity].representativeDelay;
        if (segmentValues[segmentIndex] === undefined || segmentValues[segmentIndex] < delayValue) {
            segmentValues[segmentIndex] = delayValue;
        }
    }

    return materializeIndexedSegmentValues(segmentCount, segmentValues);
}

function buildSegmentValuesFromKpiOnly(stops, kpiPoints, severityByKpiId) {
    let severity = null;
    for (const point of kpiPoints) {
        if (point.data?.fulfilled !== true) {
            continue;
        }
        const currentSeverity = severityByKpiId[String(point.tags?.kpiDefinitionID || point.data?.kpiDefinitionID || '')];
        if (currentSeverity === undefined) {
            continue;
        }
        if (severity === null || currentSeverity > severity) {
            severity = currentSeverity;
        }
    }

    const segmentValues = {};
    if (severity !== null) {
        const delayValue = severityDefinitions[severity].representativeDelay;
        for (let idx = 0; idx < (stops.length - 1); idx++) {
            segmentValues[buildSegmentKey(stops[idx], stops[idx + 1])] = delayValue;
        }
    }

    return materializeSegmentValueOrder(stops, segmentValues);
}

function buildSegmentValuesFromRawOnly(stops, rawPoints) {
    const sortedRawPoints = [...rawPoints].sort((a, b) => new Date(a.time) - new Date(b.time));
    const segmentCount = Math.max(stops.length - 1, 0);
    const segmentIndexes = buildSequentialSegmentIndexMap(sortedRawPoints, segmentCount);
    const segmentValues = {};

    for (const rawPoint of sortedRawPoints) {
        const segmentKey = getSegmentKeyFromRawPoint(rawPoint);
        if (segmentKey === null) {
            continue;
        }
        const segmentIndex = segmentIndexes.get(segmentKey);
        if (segmentIndex === undefined) {
            continue;
        }

        const delayValue = Number(rawPoint.data?.delay || 0);
        if (!Number.isFinite(delayValue) || delayValue <= 0) {
            continue;
        }

        if (segmentValues[segmentIndex] === undefined || segmentValues[segmentIndex] < delayValue) {
            segmentValues[segmentIndex] = delayValue;
        }
    }

    return materializeIndexedSegmentValues(segmentCount, segmentValues);
}

module.exports = {
    RiotIntegrationError,
    riotService: new RiotMhdService()
};
