(function () {
  var palette = ['#116a8c', '#b45309', '#b91c1c', '#0f766e'];

  function loadECharts(callback) {
    if (window.echarts) {
      callback();
      return;
    }
    loadScript(assetUrl('echarts.min.js'), callback, function () {
      loadScript('https://cdn.jsdelivr.net/npm/echarts@6/dist/echarts.min.js', callback);
    });
  }

  function loadScript(src, onload, onerror) {
    var script = document.createElement('script');
    script.src = src;
    script.onload = onload;
    if (onerror) script.onerror = onerror;
    document.head.appendChild(script);
  }

  function assetUrl(filename) {
    var scripts = document.getElementsByTagName('script');
    for (var index = scripts.length - 1; index >= 0; index -= 1) {
      var src = scripts[index].getAttribute('src') || '';
      if (src.indexOf('report_figures.js') >= 0) {
        return src.replace(/report_figures\.js(?:\?.*)?$/, filename);
      }
    }
    return 'assets/' + filename;
  }

  function formatNumber(value) {
    if (value === null || value === undefined || value === '') return 'N/A';
    var number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    return number.toLocaleString(undefined, { maximumFractionDigits: 3 });
  }

  function baseOption() {
    return {
      animation: false,
      backgroundColor: 'transparent',
      color: palette,
      textStyle: { fontFamily: 'Inter, Segoe UI, Arial, sans-serif', color: '#17202c' },
      tooltip: { trigger: 'axis', confine: true },
      aria: { enabled: true }
    };
  }

  function rollingMean(values, windowSize) {
    return values.map(function (_value, index) {
      var start = Math.max(0, index - windowSize + 1);
      var windowValues = values.slice(start, index + 1).filter(function (value) {
        return value !== null && value !== undefined && Number.isFinite(Number(value));
      });
      if (windowValues.length < 2) return null;
      var sum = windowValues.reduce(function (acc, value) { return acc + Number(value); }, 0);
      return Math.round((sum / windowValues.length) * 100) / 100;
    });
  }

  function riskColor(level) {
    var key = String(level || 'low').toLowerCase();
    return { critical: '#991b1b', high: '#b91c1c', moderate: '#b45309', low: '#0f766e' }[key] || '#0f766e';
  }

  function seriesFor(payload, figure) {
    return ((payload.data || {}).series || {})[figure.data_key || ('disease:' + figure.disease_id)];
  }

  function epidemicCurveOption(figure, payload) {
    var data = seriesFor(payload, figure);
    if (!data) return null;
    var periods = data.periods || [];
    var cases = data.cases || [];
    var visual = data.visual || {};
    var caseSeries = {
      name: payload.language === 'zh' ? '报告病例' : 'Reported cases',
      type: 'line',
      data: cases,
      symbol: 'circle',
      symbolSize: 6,
      lineStyle: { width: 2.5, color: '#116a8c' },
      itemStyle: { color: '#116a8c' },
      emphasis: { focus: 'series' }
    };
    if (Number.isFinite(Number(visual.pre_latest_median_cases))) {
      caseSeries.markLine = {
        silent: true,
        symbol: 'none',
        lineStyle: { type: 'dashed', color: '#64748b', width: 1.5 },
        label: { formatter: payload.language === 'zh' ? '最新期前中位数' : 'pre-latest median' },
        data: [{ yAxis: Number(visual.pre_latest_median_cases) }]
      };
    }
    var chartSeries = [caseSeries];
    var mean = rollingMean(cases, 3);
    if (mean.some(function (value) { return value !== null; })) {
      chartSeries.push({
        name: payload.language === 'zh' ? '3期均值' : '3-period mean',
        type: 'line',
        data: mean,
        symbol: 'none',
        connectNulls: true,
        lineStyle: { width: 2, type: 'dotted', color: '#b45309' },
        itemStyle: { color: '#b45309' }
      });
    }
    var peakIndex = periods.indexOf(String(visual.peak_period || ''));
    if (peakIndex >= 0 && visual.peak_cases !== null && visual.peak_cases !== undefined) {
      chartSeries.push({
        name: payload.language === 'zh' ? '观察峰值' : 'Observed peak',
        type: 'scatter',
        data: [[peakIndex, Number(visual.peak_cases)]],
        symbol: 'diamond',
        symbolSize: 12,
        itemStyle: { color: '#b91c1c' }
      });
    }
    var option = baseOption();
    Object.assign(option, {
      legend: { top: 0, right: 0, type: 'scroll' },
      grid: { left: 54, right: 22, top: 48, bottom: 74 },
      xAxis: {
        type: 'category',
        data: periods,
        axisLabel: { rotate: periods.length > 10 ? 35 : 0, color: '#5d6978' },
        axisLine: { lineStyle: { color: '#d7dde5' } }
      },
      yAxis: {
        type: 'value',
        name: payload.language === 'zh' ? '病例数' : 'Cases',
        min: 0,
        axisLabel: { color: '#5d6978' },
        splitLine: { lineStyle: { color: '#e7ebf0', type: 'dashed' } }
      },
      series: chartSeries
    });
    return option;
  }

  function casesIncidencePanelOption(figure, payload) {
    var data = seriesFor(payload, figure);
    if (!data) return null;
    var periods = data.periods || [];
    var incidence = data.incidence_rate_per_100k || [];
    var option = baseOption();
    Object.assign(option, {
      legend: { top: 0, right: 0, type: 'scroll' },
      grid: [
        { left: 58, right: 24, top: 48, height: '40%' },
        { left: 58, right: 24, bottom: 70, height: '30%' }
      ],
      xAxis: [
        { type: 'category', data: periods, gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: '#d7dde5' } } },
        { type: 'category', data: periods, gridIndex: 1, axisLabel: { rotate: periods.length > 10 ? 35 : 0, color: '#5d6978' }, axisLine: { lineStyle: { color: '#d7dde5' } } }
      ],
      yAxis: [
        { type: 'value', name: payload.language === 'zh' ? '病例数' : 'Cases', gridIndex: 0, min: 0, axisLabel: { color: '#5d6978' }, splitLine: { lineStyle: { color: '#e7ebf0', type: 'dashed' } } },
        { type: 'value', name: payload.language === 'zh' ? '每10万人' : 'Per 100k', gridIndex: 1, min: 0, axisLabel: { color: '#5d6978' }, splitLine: { lineStyle: { color: '#e7ebf0', type: 'dashed' } } }
      ],
      series: [
        {
          name: payload.language === 'zh' ? '报告病例' : 'Reported cases',
          type: 'bar',
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: data.cases || [],
          barMaxWidth: 18,
          itemStyle: { color: '#116a8c', opacity: 0.86 }
        },
        {
          name: payload.language === 'zh' ? '每10万人粗发病率' : 'Crude incidence per 100k',
          type: 'line',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: incidence,
          symbol: 'circle',
          symbolSize: 5,
          lineStyle: { width: 2.5, color: '#b45309' },
          itemStyle: { color: '#b45309' }
        }
      ]
    });
    return option;
  }

  function signalContextPanelOption(figure, payload) {
    var data = seriesFor(payload, figure);
    if (!data) return null;
    var cases = data.cases || [];
    var visual = data.visual || {};
    var latest = cases.length ? Number(cases[cases.length - 1] || 0) : null;
    var rows = [
      { label: payload.language === 'zh' ? '最新期病例' : 'Latest cases', value: latest, color: '#116a8c' },
      { label: payload.language === 'zh' ? '上一期病例' : 'Previous cases', value: visual.previous_cases, color: '#64748b' },
      { label: payload.language === 'zh' ? '最新期前中位数' : 'Pre-latest median', value: visual.pre_latest_median_cases, color: '#0f766e' },
      { label: payload.language === 'zh' ? '3期滚动均值' : '3-period mean', value: visual.rolling_mean_cases, color: '#b45309' },
      { label: payload.language === 'zh' ? '最近4期病例' : 'Latest 4 periods', value: visual.latest_4_period_cases, color: '#b91c1c' },
      { label: payload.language === 'zh' ? '前4期病例' : 'Previous 4 periods', value: visual.previous_4_period_cases, color: '#94a3b8' }
    ].filter(function (row) {
      return row.value !== null && row.value !== undefined && Number.isFinite(Number(row.value));
    }).reverse();
    if (!rows.length) return null;

    var subtitleParts = [];
    if (visual.last4_change_pct !== null && visual.last4_change_pct !== undefined) {
      subtitleParts.push((payload.language === 'zh' ? '近4期变化 ' : '4-period change ') + formatNumber(visual.last4_change_pct) + '%');
    }
    if (visual.latest_to_baseline_ratio !== null && visual.latest_to_baseline_ratio !== undefined) {
      subtitleParts.push((payload.language === 'zh' ? '最新/基线 ' : 'latest/baseline ') + formatNumber(visual.latest_to_baseline_ratio) + 'x');
    }
    var anomaly = visual.anomaly || {};
    if (anomaly.robust_z !== null && anomaly.robust_z !== undefined) {
      subtitleParts.push('MAD z ' + formatNumber(anomaly.robust_z));
    }

    var option = baseOption();
    Object.assign(option, {
      tooltip: {
        trigger: 'item',
        confine: true,
        formatter: function (params) {
          return params.name + '<br/>' + formatNumber(params.value) + ' cases';
        }
      },
      title: subtitleParts.length ? {
        text: subtitleParts.join(' · '),
        left: 4,
        top: 0,
        textStyle: { color: '#5d6978', fontSize: 12, fontWeight: 500 }
      } : undefined,
      grid: { left: 142, right: 28, top: subtitleParts.length ? 42 : 18, bottom: 38 },
      xAxis: {
        type: 'value',
        name: payload.language === 'zh' ? '病例数' : 'Cases',
        min: 0,
        axisLabel: { color: '#5d6978' },
        splitLine: { lineStyle: { color: '#e7ebf0', type: 'dashed' } }
      },
      yAxis: {
        type: 'category',
        data: rows.map(function (row) { return row.label; }),
        axisLabel: { color: '#5d6978' },
        axisLine: { lineStyle: { color: '#d7dde5' } }
      },
      series: [{
        name: payload.language === 'zh' ? '证据窗口' : 'Evidence window',
        type: 'bar',
        barMaxWidth: 18,
        data: rows.map(function (row) {
          return { value: Number(row.value), itemStyle: { color: row.color } };
        }),
        label: { show: true, position: 'right', formatter: function (params) { return formatNumber(params.value); }, color: '#263647' }
      }]
    });
    return option;
  }

  function recentWindowHeatmapOption(figure, payload) {
    var series = seriesFor(payload, figure);
    if (!series) return null;
    var periods = (series.periods || []).slice(-52);
    var cases = (series.cases || []).slice(-52);
    var columns = periods.length >= 13 ? 13 : Math.max(periods.length, 1);
    var rows = Math.ceil(periods.length / columns);
    var cells = [];
    periods.forEach(function (period, index) {
      cells.push([index % columns, Math.floor(index / columns), Number(cases[index] || 0), period]);
    });
    var maxValue = cells.reduce(function (max, item) { return Math.max(max, Number(item[2]) || 0); }, 0);
    var option = baseOption();
    Object.assign(option, {
      tooltip: {
        trigger: 'item',
        confine: true,
        formatter: function (params) {
          var item = params.data || [];
          return (item[3] || '') + '<br/>Cases: ' + formatNumber(item[2]);
        }
      },
      grid: { left: 70, right: 72, top: 18, bottom: 54 },
      xAxis: {
        type: 'category',
        data: Array.from({ length: columns }, function (_value, index) { return String(index + 1); }),
        name: payload.language === 'zh' ? '近期报告期序列' : 'Sequential recent periods',
        axisLabel: { color: '#5d6978' },
        axisLine: { lineStyle: { color: '#d7dde5' } }
      },
      yAxis: {
        type: 'category',
        data: Array.from({ length: rows }, function (_value, index) { return payload.language === 'zh' ? '分块 ' + (index + 1) : 'Block ' + (index + 1); }),
        axisLabel: { color: '#5d6978' },
        axisLine: { lineStyle: { color: '#d7dde5' } }
      },
      visualMap: { min: 0, max: maxValue, calculable: true, orient: 'vertical', right: 8, top: 28, inRange: { color: ['#f7fafc', '#9ecae1', '#b91c1c'] } },
      series: [{ name: payload.language === 'zh' ? '病例' : 'Cases', type: 'heatmap', data: cells, label: { show: false }, emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(17, 106, 140, 0.25)' } } }]
    });
    return option;
  }

  function riskRankingBarOption(figure, payload) {
    var rows = (((payload.data || {}).risk_ranking) || []).slice(0, 10).reverse();
    var option = baseOption();
    Object.assign(option, {
      tooltip: {
        trigger: 'item',
        confine: true,
        formatter: function (params) {
          var item = params.data || {};
          var change = item.change_pct === null || item.change_pct === undefined ? 'N/A' : formatNumber(item.change_pct) + '%';
          return params.name + '<br/>Risk: ' + formatNumber(item.value) + '<br/>Level: ' + (item.level || 'N/A') + '<br/>Latest cases: ' + formatNumber(item.latest_cases) + '<br/>Change: ' + change;
        }
      },
      grid: { left: 150, right: 24, top: 20, bottom: 44 },
      xAxis: { type: 'value', name: payload.language === 'zh' ? '风险分' : 'Risk score', min: 0, max: 100, axisLabel: { color: '#5d6978' }, splitLine: { lineStyle: { color: '#e7ebf0', type: 'dashed' } } },
      yAxis: { type: 'category', data: rows.map(function (row) { return row.name || 'Unknown'; }), axisLabel: { color: '#5d6978' }, axisLine: { lineStyle: { color: '#d7dde5' } } },
      series: [{
        name: payload.language === 'zh' ? '风险分' : 'Risk score',
        type: 'bar',
        barMaxWidth: 18,
        data: rows.map(function (row) {
          return {
            value: Number(row.risk_score || 0),
            level: row.risk_level || 'low',
            latest_cases: Number(row.latest_cases || 0),
            change_pct: row.change_pct,
            itemStyle: { color: riskColor(row.risk_level) }
          };
        })
      }]
    });
    return option;
  }

  function seasonalBaselineBandOption(figure, payload) {
    var series = seriesFor(payload, figure);
    if (!series) return null;
    var periods = series.periods || [];
    var cases = series.cases || [];
    var visual = series.visual || {};
    var derived = visual.derived || {};
    var lower = derived.baseline_lower || [];
    var upper = derived.baseline_upper || [];
    var bandWidth = upper.map(function (value, index) {
      if (value === null || value === undefined || lower[index] === null || lower[index] === undefined) return null;
      return Math.max(0, Number(value) - Number(lower[index]));
    });
    var option = baseOption();
    Object.assign(option, {
      tooltip: { trigger: 'axis', confine: true },
      legend: { top: 0, right: 0, type: 'scroll' },
      grid: { left: 56, right: 24, top: 50, bottom: 74 },
      xAxis: { type: 'category', data: periods, axisLabel: { rotate: periods.length > 10 ? 35 : 0, color: '#5d6978' }, axisLine: { lineStyle: { color: '#d7dde5' } } },
      yAxis: { type: 'value', name: payload.language === 'zh' ? '病例数' : 'Cases', min: 0, axisLabel: { color: '#5d6978' }, splitLine: { lineStyle: { color: '#e7ebf0', type: 'dashed' } } },
      series: [
        { name: payload.language === 'zh' ? '背景带下界' : 'Baseline lower', type: 'line', data: lower, stack: 'baseline-band', symbol: 'none', lineStyle: { opacity: 0 }, itemStyle: { opacity: 0 }, emphasis: { disabled: true } },
        { name: payload.language === 'zh' ? '背景带' : 'Baseline band', type: 'line', data: bandWidth, stack: 'baseline-band', symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: 'rgba(17,106,140,0.14)' }, itemStyle: { opacity: 0 }, emphasis: { disabled: true } },
        { name: payload.language === 'zh' ? '报告病例' : 'Reported cases', type: 'line', data: cases, symbol: 'circle', symbolSize: 5, lineStyle: { width: 2.5, color: '#116a8c' }, itemStyle: { color: '#116a8c' } },
        { name: payload.language === 'zh' ? '3期均值' : '3-period mean', type: 'line', data: derived.rolling_mean_3 || [], symbol: 'none', connectNulls: true, lineStyle: { width: 2, type: 'dotted', color: '#b45309' } }
      ]
    });
    if (visual.pre_latest_median_cases !== null && visual.pre_latest_median_cases !== undefined) {
      option.series[2].markLine = { silent: true, symbol: 'none', lineStyle: { type: 'dashed', color: '#64748b' }, data: [{ yAxis: Number(visual.pre_latest_median_cases) }] };
    }
    return option;
  }

  function anomalyMarkerCurveOption(figure, payload) {
    var series = seriesFor(payload, figure);
    if (!series) return null;
    var periods = series.periods || [];
    var cases = series.cases || [];
    var visual = series.visual || {};
    var derived = visual.derived || {};
    var latestIndex = Math.max(0, periods.length - 1);
    var peakIndex = periods.indexOf(String(visual.peak_period || ''));
    var option = baseOption();
    var threshold = (derived.anomaly_threshold || []).find(function (value) { return value !== null && value !== undefined; });
    Object.assign(option, {
      tooltip: { trigger: 'axis', confine: true },
      legend: { top: 0, right: 0, type: 'scroll' },
      grid: { left: 56, right: 24, top: 50, bottom: 74 },
      xAxis: { type: 'category', data: periods, axisLabel: { rotate: periods.length > 10 ? 35 : 0, color: '#5d6978' }, axisLine: { lineStyle: { color: '#d7dde5' } } },
      yAxis: { type: 'value', name: payload.language === 'zh' ? '病例数' : 'Cases', min: 0, axisLabel: { color: '#5d6978' }, splitLine: { lineStyle: { color: '#e7ebf0', type: 'dashed' } } },
      series: [
        {
          name: payload.language === 'zh' ? '报告病例' : 'Reported cases',
          type: 'line',
          data: cases,
          symbol: 'circle',
          symbolSize: 5,
          lineStyle: { width: 2.4, color: '#116a8c' },
          itemStyle: { color: '#116a8c' },
          markLine: threshold === undefined ? undefined : { silent: true, symbol: 'none', lineStyle: { type: 'dashed', color: '#b91c1c', width: 1.5 }, label: { formatter: payload.language === 'zh' ? '异常阈值' : 'alert threshold' }, data: [{ yAxis: Number(threshold) }] }
        },
        { name: payload.language === 'zh' ? '最新期' : 'Latest', type: 'scatter', data: [[latestIndex, Number(cases[latestIndex] || 0)]], symbolSize: 13, itemStyle: { color: '#b91c1c' } },
        { name: payload.language === 'zh' ? '峰值' : 'Peak', type: 'scatter', data: peakIndex >= 0 ? [[peakIndex, Number(visual.peak_cases || cases[peakIndex] || 0)]] : [], symbol: 'diamond', symbolSize: 13, itemStyle: { color: '#b45309' } }
      ]
    });
    return option;
  }

  function dataQualityTimelineOption(figure, payload) {
    var series = seriesFor(payload, figure);
    if (!series) return null;
    var periods = series.periods || [];
    var availability = (((series.visual || {}).derived || {}).availability) || {};
    var rows = [
      { key: 'cases', label: payload.language === 'zh' ? '病例' : 'Cases' },
      { key: 'deaths', label: payload.language === 'zh' ? '死亡' : 'Deaths' },
      { key: 'incidence_rate_per_100k', label: payload.language === 'zh' ? '粗发病率' : 'Crude incidence' }
    ];
    var cells = [];
    rows.forEach(function (row, rowIndex) {
      var values = availability[row.key] || [];
      periods.forEach(function (period, index) {
        cells.push([index, rowIndex, Number(values[index] || 0), period, row.label]);
      });
    });
    var option = baseOption();
    Object.assign(option, {
      tooltip: { trigger: 'item', confine: true, formatter: function (params) { var item = params.data || []; return item[4] + '<br/>' + item[3] + ': ' + (Number(item[2]) ? 'available' : 'missing'); } },
      grid: { left: 96, right: 32, top: 20, bottom: 64 },
      xAxis: { type: 'category', data: periods, axisLabel: { rotate: periods.length > 10 ? 35 : 0, color: '#5d6978' }, axisLine: { lineStyle: { color: '#d7dde5' } } },
      yAxis: { type: 'category', data: rows.map(function (row) { return row.label; }), axisLabel: { color: '#5d6978' }, axisLine: { lineStyle: { color: '#d7dde5' } } },
      visualMap: { show: false, min: 0, max: 1, inRange: { color: ['#d7dde5', '#0f766e'] } },
      series: [{ name: payload.language === 'zh' ? '可用性' : 'Availability', type: 'heatmap', data: cells, label: { show: false } }]
    });
    return option;
  }

  function riskMatrixOption(figure, payload) {
    var rows = (((payload.data || {}).risk_ranking) || []).slice(0, 12);
    if (rows.length < 2) return null;
    var maxScore = rows.reduce(function (max, row) { return Math.max(max, Number(row.risk_score || 0)); }, 1);
    var option = baseOption();
    Object.assign(option, {
      tooltip: { trigger: 'item', confine: true, formatter: function (params) { var row = params.data || {}; return row.name + '<br/>Latest cases: ' + formatNumber(row.value[0]) + '<br/>Change: ' + formatNumber(row.value[1]) + '%<br/>Risk: ' + formatNumber(row.risk_score); } },
      grid: { left: 68, right: 28, top: 30, bottom: 64 },
      xAxis: { type: 'value', name: payload.language === 'zh' ? '最新病例' : 'Latest cases', min: 0, axisLabel: { color: '#5d6978' }, splitLine: { lineStyle: { color: '#e7ebf0', type: 'dashed' } } },
      yAxis: { type: 'value', name: payload.language === 'zh' ? '较上一期变化(%)' : 'Change vs previous (%)', axisLabel: { color: '#5d6978' }, splitLine: { lineStyle: { color: '#e7ebf0', type: 'dashed' } } },
      series: [{
        name: payload.language === 'zh' ? '疾病' : 'Disease',
        type: 'scatter',
        data: rows.map(function (row) {
          return { name: row.name || 'Unknown', value: [Number(row.latest_cases || 0), Number(row.change_pct || 0)], risk_score: Number(row.risk_score || 0), itemStyle: { color: riskColor(row.risk_level) } };
        }),
        symbolSize: function (_value, params) {
          return 10 + (Number((params.data || {}).risk_score || 0) / maxScore) * 28;
        },
        label: { show: true, formatter: '{@[2]}', color: '#263647' }
      }]
    });
    option.series[0].label.formatter = function (params) { return (params.data || {}).name || ''; };
    return option;
  }

  function buildOption(figure, payload) {
    if (figure.figure_type === 'epidemic_curve') return epidemicCurveOption(figure, payload);
    if (figure.figure_type === 'signal_context_panel') return signalContextPanelOption(figure, payload);
    if (figure.figure_type === 'cases_incidence_panel') return casesIncidencePanelOption(figure, payload);
    if (figure.figure_type === 'recent_window_heatmap') return recentWindowHeatmapOption(figure, payload);
    if (figure.figure_type === 'risk_ranking_bar') return riskRankingBarOption(figure, payload);
    if (figure.figure_type === 'seasonal_baseline_band') return seasonalBaselineBandOption(figure, payload);
    if (figure.figure_type === 'anomaly_marker_curve') return anomalyMarkerCurveOption(figure, payload);
    if (figure.figure_type === 'data_quality_timeline') return dataQualityTimelineOption(figure, payload);
    if (figure.figure_type === 'risk_matrix') return riskMatrixOption(figure, payload);
    return null;
  }

  function renderFigures(payload) {
    if (!window.echarts) return;
    if (!payload) {
      return;
    }
    var byId = {};
    (payload.figures || []).forEach(function (figure) {
      if (figure && figure.id) byId[figure.id] = figure;
    });
    document.querySelectorAll('[data-report-figure-id]').forEach(function (node) {
      var figure = byId[node.getAttribute('data-report-figure-id')];
      var option = figure ? buildOption(figure, payload) : null;
      if (!option) return;
      var chart = window.echarts.init(node, null, { renderer: 'canvas' });
      chart.setOption(option, true);
      if (window.ResizeObserver) {
        var observer = new ResizeObserver(function () { chart.resize(); });
        observer.observe(node);
      } else {
        window.addEventListener('resize', function () { chart.resize(); });
      }
    });
  }

  function loadPayload(callback) {
    if (window.__GLOBALID_REPORT_FIGURE_PAYLOAD__) {
      callback(window.__GLOBALID_REPORT_FIGURE_PAYLOAD__);
      return;
    }
    var dataNode = document.getElementById('report-figures-json');
    var raw = dataNode ? (dataNode.textContent || '').trim() : '';
    if (raw) {
      try {
        callback(JSON.parse(raw));
      } catch (error) {
        console.warn('Could not parse report figure JSON', error);
      }
      return;
    }

    var src = dataNode ? dataNode.getAttribute('data-src') : '';
    if (src && window.fetch && window.location.protocol !== 'file:') {
      fetch(src)
        .then(function (response) {
          if (!response.ok) throw new Error('HTTP ' + response.status);
          return response.json();
        })
        .then(callback)
        .catch(function (error) {
          console.warn('Could not load report figure payload ' + src, error);
        });
      return;
    }
    console.warn('No report figure payload was available' + (src ? ': ' + src : ''));
  }

  loadPayload(function (payload) {
    loadECharts(function () {
      renderFigures(payload);
    });
  });
})();
