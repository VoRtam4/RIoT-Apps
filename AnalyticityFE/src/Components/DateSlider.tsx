import { Slider, SliderSingleProps } from 'antd';
import dayjs from 'dayjs';
import React, { useContext, useEffect, useState } from 'react';
import { colorRed } from '../utils/constants';
import { filterContext } from '../utils/contexts';

type rangeValuesType = {
  startValue: number;
  endValue: number;
};

const DateSlider = () => {
  const { filter, setNewFilter, dataAvailability } = useContext(filterContext);
  const sliderStartDate = dayjs(dataAvailability?.oldestDate ?? dayjs().subtract(1, 'year').format('YYYY-MM-DD'));
  const sliderEndDate = dayjs(dataAvailability?.newestDate ?? dayjs().format('YYYY-MM-DD'));
  const sliderDaySpan = Math.max(sliderEndDate.diff(sliderStartDate, 'days'), 1);

  const [value, setValue] = useState<rangeValuesType>({
    startValue: 0,
    endValue: sliderDaySpan,
  });

  const marks: SliderSingleProps['marks'] = {
    0: {
      style: { color: colorRed },
      label: <strong>{sliderStartDate.format('DD.MM.YYYY')}</strong>,
    },

    [sliderDaySpan]: {
      style: { color: colorRed },
      label: <strong>{sliderEndDate.format('DD.MM.YYYY')}</strong>,
    },
  };

  const handleChange = (value) => {
    setValue({ startValue: value[0], endValue: value[1] });
  };

  useEffect(() => {
    if (!filter) {
      return;
    }
    const endValue = dayjs(filter?.toDate).diff(sliderStartDate, 'days');
    const startValue = dayjs(filter?.fromDate).diff(sliderStartDate, 'days');
    setValue({ startValue, endValue });
  }, [filter, dataAvailability]);

  return (
    <div style={{ width: '95%', margin: 'auto' }}>
      <Slider
        range={{ draggableTrack: false }}
        marks={marks}
        min={0}
        max={sliderDaySpan}
        step={1}
        value={[value.startValue, value.endValue]}
        onChange={handleChange}
        className="date-slider"
        tooltip={{
          formatter: (value) => {
            return sliderStartDate.add(value, 'days').format('DD.MM.YYYY');
          },
        }}
        onChangeComplete={(value) => {
          setNewFilter((prevState) => ({
            ...prevState,
            fromDate: sliderStartDate.add(value[0], 'days').format('YYYY-MM-DD'),
            toDate: sliderStartDate.add(value[1], 'days').format('YYYY-MM-DD'),
          }));
        }}
      />
    </div>
  );
};

export default DateSlider;
