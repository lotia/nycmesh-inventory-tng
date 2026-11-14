import { fireEvent, render, screen } from "@testing-library/preact";
import { expect, test, vi } from "vitest";
import { BUILD } from "./build";
import { Outbox, STORAGE_KEY } from "./outbox";
import { RegisterPage } from "./RegisterPage";
import { REFUSALS } from "./registration";
import { answering, deferred, json } from "./test-fetch";

function openAt(path: string) {
  window.history.replaceState(null, "", path);
}

const phrase = () =>
  screen.getByLabelText<HTMLInputElement>("Registration phrase");
const button = () => screen.getByRole<HTMLButtonElement>("button");
const registered = () => json({ device_id: 4 }, 201);

test("the QR code's link fills in the phrase, and nothing is sent until asked", () => {
  const { api, sent } = answering(registered);
  openAt("/register?code=retry-ebay-zookeeper-demotion-comprised");
  render(<RegisterPage api={api} />);
  expect(phrase().value).toBe("retry-ebay-zookeeper-demotion-comprised");
  expect(sent).toEqual([]);
  // The inventory records; "save" is not the scan page's word (Ali,
  // inventory-tng-wyq).
  screen.getByText(/^Registering lets this browser record in the inventory\./);
});

test("registering says so, links to the scan page and drops the phrase", async () => {
  const { api, sent } = answering(registered);
  openAt("/register?code=a-b-c-d-e&from=qr");
  render(<RegisterPage api={api} />);
  fireEvent.submit(button());
  expect((await screen.findByRole("status")).textContent).toBe(
    "This browser is registered.",
  );
  expect(await sent[0]?.json()).toEqual({ code: "a-b-c-d-e" });
  expect(screen.getByRole("link").getAttribute("href")).toBe("/scan");
  expect(window.location.search).toBe("?from=qr");
});

test("a typed phrase is sent without the spaces around it", async () => {
  const { api, sent } = answering(registered);
  openAt("/register");
  render(<RegisterPage api={api} />);
  fireEvent.input(phrase(), { target: { value: "  a-b-c-d-e \n" } });
  fireEvent.submit(button());
  await screen.findByRole("status");
  expect(await sent[0]?.json()).toEqual({ code: "a-b-c-d-e" });
});

test("a blank phrase is not sent, and the page says what to do", async () => {
  const { api, sent } = answering(registered);
  openAt("/register?code=%20%20");
  render(<RegisterPage api={api} />);
  fireEvent.submit(button());
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Enter the phrase you were given.",
  );
  expect(sent).toEqual([]);
});

test("the button waits while the phrase is checked", async () => {
  const reply = deferred();
  const { api } = answering(() => reply.promise);
  openAt("/register?code=x");
  render(<RegisterPage api={api} />);
  fireEvent.submit(button());
  expect(button().disabled).toBe(true);
  expect(button().textContent).toBe("Registering…");
  reply.resolve(json({ reason: "expired" }, 400));
  await screen.findByRole("alert");
  expect(button().disabled).toBe(false);
});

test("a second tap while the first is sent does not send again", async () => {
  const reply = deferred();
  const { api, sent } = answering(() => reply.promise);
  openAt("/register?code=x");
  render(<RegisterPage api={api} />);
  fireEvent.submit(button());
  fireEvent.submit(button());
  reply.resolve(registered());
  await screen.findByRole("status");
  expect(sent).toHaveLength(1);
});

test("a refusal is explained, and the phrase stays to correct", async () => {
  const { api } = answering(() => json({ reason: "used_up" }, 400));
  openAt("/register?code=x");
  render(<RegisterPage api={api} />);
  fireEvent.submit(button());
  expect((await screen.findByRole("alert")).textContent).toBe(REFUSALS.used_up);
  expect(phrase().value).toBe("x");
  expect(window.location.search).toBe("?code=x");
});

test("trying again clears the last refusal while it waits", async () => {
  const second = deferred();
  const replies = [json({ reason: "expired" }, 400)];
  const { api } = answering(() => replies.shift() ?? second.promise);
  openAt("/register?code=x");
  render(<RegisterPage api={api} />);
  fireEvent.submit(button());
  await screen.findByRole("alert");
  fireEvent.submit(button());
  expect(screen.queryByRole("alert")).toBeNull();
  second.resolve(json({ reason: "expired" }, 400));
  expect((await screen.findByRole("alert")).textContent).toBe(REFUSALS.expired);
});

test.each([
  [
    "an unavailable database",
    () => json({ reason: "database_unavailable" }, 503),
    "can't answer just now",
  ],
  [
    "no connection",
    () => Promise.reject(new TypeError("Failed to fetch")),
    "Couldn't reach",
  ],
])("%s is explained", async (_name, reply, words) => {
  const { api } = answering(reply);
  openAt("/register?code=x");
  render(<RegisterPage api={api} />);
  fireEvent.submit(button());
  expect((await screen.findByRole("alert")).textContent).toContain(words);
});

test("registering sends the batches that waited for it", async () => {
  const { api, sent } = answering((request) =>
    new URL(request.url).pathname === "/api/devices"
      ? json({ device_id: 4 }, 201)
      : json({ transaction_id: 12 }, 201),
  );
  const waiting = {
    body: {
      key: "0b6c4c5e-7d1a-4c55-9f4e-2f4a8d3c1b2a",
      kind: "receipt",
      volunteer_id: 1,
      to_location_id: 2,
      lines: [{ item_id: 5, quantity: "1" }],
    },
    what: "Receipt: 1 item",
    queuedAt: 1,
    build: BUILD,
    state: "waiting",
    waitingFor: "registration",
    reason: { code: "not_registered", why: "unregistered" },
    transactionId: null,
    failures: 0,
  };
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({ build: BUILD, entries: [waiting] }),
  );
  const outbox = new Outbox(api);
  openAt("/register?code=a-b-c-d-e");
  render(<RegisterPage api={api} outbox={outbox} />);
  fireEvent.submit(button());
  await screen.findByText("The batch waiting on this browser is being sent.");
  await vi.waitFor(() => expect(outbox.entries.value[0]?.state).toBe("saved"));
  expect(await sent[1]?.json()).toEqual(waiting.body);
});
