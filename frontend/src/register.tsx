import { mount } from "./mount";
import { RegisterPage } from "./RegisterPage";
import { telemetry } from "./telemetry";

telemetry.start("register");

mount(<RegisterPage />);
