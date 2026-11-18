import { mount } from "./mount";
import { RegisterPage } from "./register/RegisterPage";
import { telemetry } from "./telemetry";

telemetry.start("register");

mount(<RegisterPage />);
