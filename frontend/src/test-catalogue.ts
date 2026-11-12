/** A small catalogue for the tests. Never bundled. */
import type { Catalogue, Item } from "./cart";

export const radio: Item = {
  id: 100,
  name: "LiteBeam 5AC",
  unit: "each",
  category: "Radios",
  manufacturer: "Ubiquiti",
  part_number: "LBE-5AC-Gen2",
  old_code: "LB5",
  search_text: "LiteBeam 5AC Ubiquiti LBE-5AC-Gen2 LB5 Radios",
  packages: [
    {
      id: 1000,
      name: "Box",
      size: "4.000",
      track_opened: false,
      retired: false,
      short_below: null,
      sealed: [{ location_id: 10, count: 2 }],
    },
  ],
  retired: false,
};

export const cable: Item = {
  id: 101,
  name: "Cat6 cable",
  unit: "m",
  category: "Cables",
  manufacturer: null,
  part_number: null,
  old_code: null,
  search_text: "Cat6 cable Cables",
  packages: [
    {
      id: 1001,
      name: "Spool",
      size: "305.000",
      track_opened: true,
      retired: false,
      short_below: null,
      sealed: [
        { location_id: 10, count: 3 },
        { location_id: 12, count: 1 },
      ],
    },
  ],
  retired: false,
};

/** Accented, with its text folded as the server folds it (models.fold). */
const cafe: Item = {
  ...cable,
  id: 102,
  name: "Café Łódź patch",
  category: "Câbles",
  search_text: "Cafe Lodz patch Cables",
  packages: [],
};

export const CATALOGUE: Catalogue = {
  settings: { name_check_minutes: 15 },
  volunteers: [
    { id: 1, name: "Ada" },
    { id: 2, name: "Grace" },
  ],
  locations: [
    { id: 10, name: "Hub", kind: "store", parent_id: null },
    { id: 11, name: "Shelf A", kind: "area", parent_id: 10 },
    { id: 12, name: "Van", kind: "vehicle", parent_id: null },
  ],
  items: [radio, cable],
  open_packages: [],
  saved: [],
  labels: [
    { code: "K7QX2M", item_id: 101, package_id: 1001, location_id: null },
    { code: "B3ZT9W", item_id: 100, package_id: null, location_id: null },
    { code: "S4HELF", item_id: null, package_id: null, location_id: 11 },
  ],
};

/** The catalogue, and the accented item. */
export const WITH_CAFE: Catalogue = {
  ...CATALOGUE,
  items: [...CATALOGUE.items, cafe],
};
