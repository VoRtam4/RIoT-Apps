import React from 'react';
import backendApi from './api';
import { DataAvailability, FILTER_DEFAULT_VALUE, Filter, FilterContext } from './contexts';

export const useFilter = (): FilterContext => {
  const [filter, setFilter] = React.useState<Filter>(FILTER_DEFAULT_VALUE.filterDefaultValue);
  const [dataAvailability, setDataAvailability] = React.useState<DataAvailability | null>(null);

  const filterDefaultValue = FILTER_DEFAULT_VALUE.filterDefaultValue;

  const setNewFilter = React.useCallback((newFilter: Filter): void => {
    setFilter(newFilter);
  }, []);

  React.useEffect(() => {
    backendApi
      .get('/data_availability/')
      .then((response) => {
        const availability = response.data as DataAvailability;
        setDataAvailability(availability);
        setFilter((prevState) => {
          const nextFromDate =
            prevState?.fromDate && prevState.fromDate >= availability.oldestDate
              ? prevState.fromDate
              : availability.suggestedFromDate;
          const nextToDate =
            prevState?.toDate && prevState.toDate <= availability.newestDate
              ? prevState.toDate
              : availability.suggestedToDate;
          return {
            ...prevState,
            fromDate: nextFromDate,
            toDate: nextToDate,
          };
        });
      })
      .catch(() => {
        setDataAvailability(null);
      });
  }, []);

  return {
    filter,
    filterDefaultValue,
    dataAvailability,
    setNewFilter,
  };
};
