import { RegisterPage } from "./register/RegisterPage";
import { mount } from "./shared/mount";
import { telemetry } from "./shared/telemetry";

telemetry.start("register");

mount(<RegisterPage />);
