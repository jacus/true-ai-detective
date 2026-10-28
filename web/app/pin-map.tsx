"use client";

import "leaflet/dist/leaflet.css";
import {
  CircleMarker,
  MapContainer,
  TileLayer,
  Tooltip,
  useMapEvents,
} from "react-leaflet";
import type { Evidence } from "@/lib/api";

export type LatLng = [number, number];

// Where an empty case's map starts: Europe, zoomed out.
export const START: LatLng = [50, 15];

type Props = {
  pins: Evidence[];
  draft: LatLng | null;
  onPick: (where: LatLng) => void;
};

/** OpenStreetMap via Leaflet: shows the case's pins; a click picks the spot for a new one. */
export default function PinMap({ pins, draft, onPick }: Props) {
  const last = pins.at(-1);
  return (
    <MapContainer
      center={last ? [last.latitude ?? 0, last.longitude ?? 0] : START}
      zoom={last ? 11 : 3}
      style={{ height: "100%" }}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {pins.map((pin) => (
        <CircleMarker
          key={pin.id}
          center={[pin.latitude ?? 0, pin.longitude ?? 0]}
          radius={8}
          pathOptions={{ color: "#b03a2e" }}
        >
          <Tooltip>{pin.label}</Tooltip>
        </CircleMarker>
      ))}
      {draft && (
        <CircleMarker
          center={draft}
          radius={8}
          pathOptions={{ color: "#1f3a5f", dashArray: "4" }}
        />
      )}
      <PickOnClick onPick={onPick} />
    </MapContainer>
  );
}

function PickOnClick({ onPick }: { onPick: (where: LatLng) => void }) {
  useMapEvents({
    click: (event) => onPick([event.latlng.lat, event.latlng.lng]),
  });
  return null;
}
