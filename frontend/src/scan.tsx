import { mount } from "./mount";
import { ScanPage } from "./ScanPage";
import { telemetry } from "./telemetry";

telemetry.start("scan");

mount(<ScanPage reload={() => location.reload()} />);
