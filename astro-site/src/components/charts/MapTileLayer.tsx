import { useEffect, useMemo, useState } from 'react';
import './map-tiles.css';

type MapLanguage = 'en' | 'zh' | 'fr';
type TileProvider = 'osm' | 'amap';
type TileStyle = 'standard' | 'satellite' | 'dark';

const TILE_COUNT = 4;
const TILE_ZOOM = 2;

function tileUrl(provider: TileProvider, style: TileStyle, x: number, y: number): string {
  if (provider === 'amap') {
    const shard = (x + y) % 4;
    const amapStyle = style === 'satellite' ? 6 : style === 'dark' ? 8 : 7;
    return `https://webrd0${shard}.is.autonavi.com/appmaptile?style=${amapStyle}&x=${x}&y=${y}&z=${TILE_ZOOM}`;
  }
  if (style === 'satellite') return `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${TILE_ZOOM}/${y}/${x}`;
  if (style === 'dark') return `https://a.basemaps.cartocdn.com/dark_all/${TILE_ZOOM}/${x}/${y}.png`;
  return `https://tile.openstreetmap.org/${TILE_ZOOM}/${x}/${y}.png`;
}

/**
 * A lightweight, language-aware raster context for the ECharts maps.
 * ECharts remains responsible for the data projection and roam gestures;
 * this layer supplies geographic context and can be switched without
 * rebuilding the chart. It is deliberately pointer-transparent so dragging
 * and wheel zoom continue to belong to the map below it.
 */
export default function MapTileLayer({ language }: { language: MapLanguage }) {
  const [provider, setProvider] = useState<TileProvider>(language === 'zh' ? 'amap' : 'osm');
  const [style, setStyle] = useState<TileStyle>('standard');
  useEffect(() => setProvider(language === 'zh' ? 'amap' : 'osm'), [language]);
  const labels = useMemo(() => language === 'zh'
    ? { label: '底图', provider: '地图源', style: '样式', standard: '常规', satellite: '卫星', dark: '暗色', osm: 'OpenStreetMap', amap: '高德地图', attribution: provider === 'osm' ? (style === 'satellite' ? '© Esri' : style === 'dark' ? '© CARTO' : '© OpenStreetMap contributors') : '© 高德地图' }
    : language === 'fr'
      ? { label: 'Fond de carte', provider: 'Source', style: 'Style', standard: 'Standard', satellite: 'Satellite', dark: 'Sombre', osm: 'OpenStreetMap', amap: 'Amap', attribution: provider === 'osm' ? (style === 'satellite' ? '© Esri' : style === 'dark' ? '© CARTO' : '© contributeurs OpenStreetMap') : '© Amap' }
      : { label: 'Basemap', provider: 'Source', style: 'Style', standard: 'Standard', satellite: 'Satellite', dark: 'Dark', osm: 'OpenStreetMap', amap: 'Amap', attribution: provider === 'osm' ? (style === 'satellite' ? '© Esri' : style === 'dark' ? '© CARTO' : '© OpenStreetMap contributors') : '© Amap' }, [language, provider, style]);
  const tiles = useMemo(() => Array.from({ length: TILE_COUNT * TILE_COUNT }, (_, index) => {
    const x = index % TILE_COUNT;
    const y = Math.floor(index / TILE_COUNT);
    return { x, y, src: tileUrl(provider, style, x, y) };
  }), [provider, style]);
  return (
    <>
      <div className={`map-tile-layer is-${style}`} aria-hidden="true">
        <div className="map-tile-world">
          {tiles.map(tile => <img key={`${provider}-${style}-${tile.x}-${tile.y}`} src={tile.src} alt="" loading="eager" draggable={false} />)}
        </div>
      </div>
      <div className="map-tile-control" role="group" aria-label={labels.label}>
        <label>
          <span className="sr-only">{labels.provider}</span>
          <select value={provider} onChange={event => setProvider(event.target.value as TileProvider)}>
            <option value="osm">{labels.osm}</option>
            <option value="amap">{labels.amap}</option>
          </select>
        </label>
        <label>
          <span className="sr-only">{labels.style}</span>
          <select value={style} onChange={event => setStyle(event.target.value as TileStyle)}>
            <option value="standard">{labels.standard}</option>
            <option value="satellite">{labels.satellite}</option>
            <option value="dark">{labels.dark}</option>
          </select>
        </label>
        <small>{labels.attribution}</small>
      </div>
    </>
  );
}
