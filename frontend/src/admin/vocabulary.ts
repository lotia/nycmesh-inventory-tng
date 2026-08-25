/**
 * A small list an item or a batch chooses from, and the two ways it changes.
 *
 * THE PLACEMENT ARGUMENT, STATED ONCE. Neither a category nor a location is
 * somewhere anybody navigates to in a stock-taking app. They are fields —
 * a category on an item, a location on a movement — and the moment it is worth
 * making one is the moment somebody choosing from the list finds the row they
 * want is not in it. So the controls for making and correcting one live beside
 * the select rather than on a screen of their own, which is decision 0014
 * point 1's principle applied to a vocabulary rather than to a row.
 *
 * WHAT THIS HOLDS is the part both of them do identically: the list, whether
 * anything is being made or corrected, and the re-read that follows a save.
 * What differs is one line each — which row the caller then uses — so that is
 * the argument rather than a second copy of everything around it.
 */
import { useState } from "react";
import type { ApiError } from "../api/client";
import type { Page } from "../api/types";
import { useResource } from "../api/useResource";

/** The least a row has to be for this to offer it and hand it back. */
interface Row {
  id: number;
}

export interface Vocabulary<T> {
  /** What the list currently offers. */
  rows: T[];
  /** The read that failed, if it did. */
  error: ApiError | null;
  /** True while the read is in flight, which is not the same as an empty list. */
  loading: boolean;
  /**
   * What is being made or corrected, if anything.
   *
   * One state with two shapes rather than two flags: `{ row: null }` is a new
   * one and `{ row }` is that row. Two booleans kept in step are two that can
   * come apart.
   */
  editing: { row: T | null } | null;
  /** Start making one. */
  add: () => void;
  /** Start correcting this one. */
  correct: (row: T) => void;
  /** Put the form away without saving. */
  close: () => void;
  /** One was saved: read the list again, and tell the caller which. */
  settled: (saved: T) => void;
}

export function useVocabulary<T extends Row>(path: string, chose: (row: T) => void): Vocabulary<T> {
  const [editing, setEditing] = useState<{ row: T | null } | null>(null);
  // The `reload` argument `useResource` takes; its own comment says what a
  // caller changing this is asking for.
  const [changed, setChanged] = useState(0);
  const { data, error, loading } = useResource<Page<T>>(path, changed);

  return {
    rows: data?.results ?? [],
    error,
    loading,
    editing,
    add: () => setEditing({ row: null }),
    correct: (row: T) => setEditing({ row }),
    close: () => setEditing(null),
    settled: (saved: T) => {
      setChanged((count) => count + 1);
      // Handed to the caller rather than merely re-read, because somebody who
      // has just made one has made the one they wanted: an item joins the
      // grouping, a batch is recorded at the place. Correcting the row already
      // in use leaves it in use.
      chose(saved);
    },
  };
}
