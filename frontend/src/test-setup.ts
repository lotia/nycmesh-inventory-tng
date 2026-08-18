// Adds jest-dom matchers (toBeInTheDocument, toHaveTextContent, ...) to
// Vitest's expect, and unmounts React trees between tests.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);
