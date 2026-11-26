# SPDX-FileCopyrightText: 2026 The weft authors
# SPDX-License-Identifier: MIT
"""The CM5 lab carrier as code (weft-design docs/22).

Starts from the Raspberry Pi CM5 IO Board circuit (ioboard.json, imported from its KiCad files)
and changes it:
  * power input: USB-C PD negotiated by an STUSB4500, measured (INA228) and switched (TPS25947
    eFuse) before the CM5's 5 V; the CM5's own CC pins stay unconnected (docs/22 §3.1);
  * GPIO_VREF from a TPS2116 power mux (3.3 V or 1.8 V bank) instead of a 0 Ω resistor;
  * USB 3 VBUS, M.2 power and the RTC battery can be cut by the supervisor;
  * the CM5's USB 2.0 port goes through a mux to the lab hub or the ESP32-S3;
and adds the supervisor (RP2350B), the GPIO path (LSF0108), control (TCA9555 + open-drain
buffers), the USB hub, the ESP32-S3 and headers for every CM5 pin.

Rule R5 (never back-power the CM5): every path into a CM5 pin is open-drain, or a pass FET that
is off while the CM5's 3.3 V is missing. Nothing here drives a CM5 pin from a supervisor rail.
"""

import json
import os

from circuit import Circuit

HERE = os.path.dirname(os.path.abspath(__file__))

SUP = "SUP_3V3"  # supervisor 3.3 V, alive whenever the PC or the power input is connected


def ioboard(c):
    """The IO Board circuit, with the changes listed in the module docstring."""
    data = json.load(open(os.path.join(HERE, "ioboard.json"), encoding="utf-8"))
    rename = {"FB": "M2_BUCK_FB", "VBUS": "USB3_VBUS", "PWM": "FAN_PWM", "TACHO": "FAN_TACHO",
              "USBOTG": "USB_OTG_ID", "nPWR_LED": "LED_nPWR", "IO_Module1A_LED_nACT": "LED_nACT",
              "ETH_LEDG": "ETH_nLED2", "ETH_LEDY": "ETH_nLED3", "WL_nDis": "WL_nDISABLE",
              "BT_nDis": "BT_nDISABLE", "PMIC_ENABLE": "PMIC_EN"}
    c.start_block("IO Board", 0)
    for ref, d in sorted(data.items()):
        lib_id = d["lib_id"] if not d["lib_id"].startswith(":") else "Device:LED"
        p = c.part(lib_id, d["value"], ref=ref, footprint=d["footprint"], **d["fields"])
        for pin, net in d["pins"].items():
            p.nets[pin] = rename.get(net, net)
    P = c.parts
    # Power input: J11 now feeds the PD controller and the eFuse, not the CM5 directly.
    j11 = P["J11"]
    j11["VBUS"] = "VBUS_IN"
    j11["CC1"], j11["CC2"] = "PD_CC1", "PD_CC2"
    j11["D+"] = None
    j11["D-"] = None
    m = P["Module1"]
    m["94"] = None  # CC1: the carrier negotiates (docs/22 §3.1)
    m["96"] = None  # CC2
    # GPIO_VREF: the TPS2116 mux replaces R4 (1.8 V, not fitted) and R5 (3.3 V, 0 Ω).
    del P["R4"], P["R5"]
    # USB 3 VBUS and M.2 power: CM5 enable through 1 kΩ, so the supervisor can pull it low.
    P["U6"]["3"] = "VBUS_EN_SW"
    P["U8"]["7"] = "PCIE_PWR_EN_SW"
    P["R14"]["1"] = "PCIE_PWR_EN_SW"
    # RTC battery through a load switch.
    P["BT1"]["1"] = "VBAT_CELL"
    return m


def power(c):
    c.start_block("Power", 100)
    # USB PD sink: 5 V, the current set in the NVM (5 A); dead-battery Rd on CC.
    u = c.part("Interface_USB:STUSB4500QTR", "STUSB4500QTR", MPN="STUSB4500QTR", Manufacturer="STMicroelectronics")
    u.connect(VDD="VBUS_IN", VSYS="GND", CC1="PD_CC1", CC1DB="PD_CC1", CC2="PD_CC2", CC2DB="PD_CC2",
              RESET="GND", ADDR0="GND", ADDR1="GND", SCL="SUP_SCL", SDA="SUP_SDA", ALERT="SUP_nINT",
              ATTACH="PD_ATTACH", GND="GND", VREG_2V7="PD_VREG_2V7", VREG_1V2="PD_VREG_1V2",
              VBUS_VS_DISCH="PD_VBUS_SENSE")
    c.R("1k", "VBUS_IN", "PD_VBUS_SENSE")
    c.C("1u", "PD_VREG_2V7")
    c.C("1u", "PD_VREG_1V2")
    c.C("1u", "VBUS_IN")
    c.C("10u", "VBUS_IN", size="0805")
    c.R("10k", "PD_ATTACH", SUP)
    # Input current and voltage: 5 mΩ shunt, INA228 at 0x40.
    c.R("5m", "VBUS_IN", "VBUS_SHUNT", size="2512", MPN="CSS2H-2512R-5L00F", Manufacturer="Bourns",
        Near="255.0,145.5,90")
    ina = c.part("Sensor_Energy:INA228", "INA228AIDGSR", MPN="INA228AIDGSR", Manufacturer="TI")
    ina.connect(**{"Vin+": "VBUS_IN", "Vin-": "VBUS_SHUNT", "Vbus": "+5V_CM5", "VS": SUP, "GND": "GND",
                   "A0": "GND", "A1": "GND", "SDA": "SUP_SDA", "SCL": "SUP_SCL", "~{Alert}": "SUP_nINT"})
    c.C("100n", SUP)
    # eFuse: 5.5 A (RILM = 3334 / 5.5 = 606 Ω), OVLO at 5.9 V, off until the supervisor enables it.
    e = c.part("weft:TPS25947", "TPS259470ARPWR", MPN="TPS259470ARPWR", Manufacturer="TI", Near="256.5,138.0,0")
    e.connect(IN="VBUS_SHUNT", OUT="+5V_CM5", **{"EN/UVLO": "EFUSE_EN", "OVLO": "EFUSE_OVLO",
              "AUXOFF": "EFUSE_AUXOFF", "~{FLT}": "EFUSE_nFLT", "DVDT": "EFUSE_DVDT", "ILM": "EFUSE_ILM",
              "GND": "GND"})
    c.R("100k", "EFUSE_EN", "GND")
    c.R("390k", "VBUS_SHUNT", "EFUSE_OVLO")
    c.R("100k", "EFUSE_OVLO", "GND")
    c.R("10k", "EFUSE_AUXOFF", SUP)
    c.R("10k", "EFUSE_nFLT", SUP)
    c.C("10n", "EFUSE_DVDT")
    c.R("604", "EFUSE_ILM", "GND", MPN="RC0402FR-07604RL")
    c.C("10u", "VBUS_SHUNT", size="0805")
    c.C("100u", "+5V_CM5", size="1206")
    # Fast discharge of the CM5's 5 V for real power cuts: 10 Ω, about 0.5 A at the start.
    q = c.part("Transistor_FET:AO3400A", "AO3400A", prefix="Q", MPN="AO3400A")
    q.connect(G="DISCHARGE", S="GND", D="DISCH_R")
    c.R("10", "+5V_CM5", "DISCH_R", size="1206")
    c.R("100k", "DISCHARGE", "GND")
    # Supervisor supply: either input, through Schottky diodes.
    for src in ("VBUS_IN", "VBUS_LAB"):
        d = c.part("Diode:PMEG3010EJ", "PMEG3010EJ", prefix="D", MPN="PMEG3010EJ,115")
        d.connect(A=src, K="SUP_5V")
    c.C("10u", "SUP_5V", size="0805")
    for out, label in ((SUP, "SUP"), ("ESP_3V3", "ESP")):
        r = c.part("Regulator_Linear:AP2112K-3.3", "AP2112K-3.3TRG1", MPN="AP2112K-3.3TRG1")
        r.connect(VIN="SUP_5V", EN="SUP_5V", GND="GND", VOUT=out)
        c.C("1u", "SUP_5V")
        c.C("10u", out, size="0805")
    # GPIO_VREF: 3.3 V unless the supervisor pulls PR1 low (1.8 V bank). Only while the CM5 is off.
    mux = c.part("Power_Management:TPS2116DRL", "TPS2116DRLR", MPN="TPS2116DRLR", Near="203,100")
    mux.connect(VIN1="+3V3_CM5", VIN2="+1V8_CM5", VOUT="GPIO_VREF", MODE="+3V3_CM5", PR1="VREF_PR1",
                GND="GND")
    c.R("100k", "VREF_PR1", "+3V3_CM5", Near="203,100")
    c.C("1u", "GPIO_VREF", Near="203,100")
    # CM5 rails (INA3221 at 0x41): voltages of 3.3 V, 1.8 V and 5 V. Revision 0.1 has no shunts in
    # the module's 3.3 V and 1.8 V outputs (they would split the IO Board's rail routing); the
    # inputs are wired so shunts can be added later without changing the chip.
    i3 = c.part("Power_Management:INA3221", "INA3221AIRGVR", MPN="INA3221AIRGVR")
    i3.connect(**{"IN+1": "+3V3_CM5", "IN-1": "+3V3_CM5", "IN+2": "+1V8_CM5", "IN-2": "+1V8_CM5",
                  "IN+3": "+5V_CM5", "IN-3": "+5V_CM5", "VS": SUP, "GND": "GND", "EP": "GND", "A0": SUP,
                  "SCL": "SUP_SCL", "SDA": "SUP_SDA"})
    c.C("100n", SUP)
    # USB 3 VBUS and M.2 power: the CM5's enable through 1 kΩ; an open-drain buffer may pull low.
    c.R("1k", "VBUS_EN", "VBUS_EN_SW", Near="U6")
    c.R("1k", "PCIE_PWR_EN", "PCIE_PWR_EN_SW", Near="U8")
    # RTC battery switch: on by default (1 MΩ from the cell), off when the supervisor pulls ON low.
    sw = c.part("Power_Management:TPS22919DCK", "TPS22919DCKR", MPN="TPS22919DCKR", Near="BT1")
    sw.connect(IN="VBAT_CELL", OUT="VBAT", ON="VBAT_ON", GND="GND")
    c.R("1M", "VBAT_ON", "VBAT_CELL", Near="BT1")


def supervisor(c):
    c.start_block("Supervisor", 200)
    u = c.part("MCU_RaspberryPi:RP2350B", "RP2350B", MPN="RP2350B", Manufacturer="Raspberry Pi")
    for name in ("IOVDD", "QSPI_IOVDD", "USB_OTP_VDD", "ADC_AVDD", "VREG_VIN"):
        u[name] = SUP
    u.connect(DVDD="+1V1_SUP", VREG_FB="+1V1_SUP", VREG_LX="SUP_VREG_LX", VREG_AVDD="SUP_VREG_AVDD",
              VREG_PGND="GND", GND="GND", XIN="SUP_XIN", XOUT="SUP_XOUT", USB_DP="SUP_USB_DP_R",
              USB_DM="SUP_USB_DM_R", SWCLK="SUP_SWCLK", SWDIO="SUP_SWDIO", RUN="SUP_RUN",
              QSPI_SCLK="SUP_QSPI_SCLK", QSPI_SD0="SUP_QSPI_SD0",
              QSPI_SD1="SUP_QSPI_SD1", QSPI_SD2="SUP_QSPI_SD2", QSPI_SD3="SUP_QSPI_SD3")
    u["~{QSPI_SS}"] = "SUP_QSPI_SS"
    # GPIO0..27 face the CM5's GPIO0..27 one to one (through the GPIO path).
    for i in range(28):
        u[f"GPIO{i}"] = f"SUP_GPIO{i}"
    misc1 = ["SUP_LED_nACT", "SUP_LED_nPWR", "SUP_FAN_PWM", "SUP_FAN_TACHO", "SUP_SYNC_OUT",
             "SUP_ETH_nLED2", "SUP_CAM_GPIO0", "SUP_CAM_GPIO1"]
    for i, net in enumerate(misc1):
        u[f"GPIO{28 + i}"] = net
    u["GPIO36"], u["GPIO37"] = "SUP_SDA", "SUP_SCL"  # I2C0
    u["GPIO38"], u["GPIO39"] = "DDC_SUP_SDA", "DDC_SUP_SCL"  # I2C1: monitor emulation on HDMI0
    u["GPIO40"], u["GPIO41"] = "ESP_RXD", "ESP_TXD"  # UART1 TX/RX to the ESP32-S3
    u["GPIO42"] = "SUP_nINT"
    u["GPIO43"] = "SUP_LED"
    u["GPIO44"], u["GPIO45"] = "DBG_A", "DBG_B"  # module debug UART/SWD (pogo pins, later)
    u["GPIO46"], u["GPIO47"] = "SUP_AUX46", "SUP_AUX47"
    # Core regulator (RP2350 hardware design guide): 3.3 µH, 4.7 µF out; VREG_AVDD RC filter.
    c.part("Device:L", "3.3u", prefix="L", footprint="Inductor_SMD:L_1008_2520Metric",
           MPN="AOTA-B201610S3R3-101-T").connect(**{"1": "SUP_VREG_LX", "2": "+1V1_SUP"})
    c.C("4.7u", "+1V1_SUP", size="0603")
    c.C("4.7u", SUP, size="0603")
    c.R("33", SUP, "SUP_VREG_AVDD")
    c.C("4.7u", "SUP_VREG_AVDD", size="0603")
    for _ in range(9):
        c.C("100n", SUP)
    for _ in range(3):
        c.C("100n", "+1V1_SUP")
    # 12 MHz crystal, 1 kΩ in series with XOUT.
    y = c.part("Device:Crystal_GND24", "12MHz", prefix="Y", footprint="Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm",
               MPN="ABM8-272-T3")
    y.connect(**{"1": "SUP_XIN", "3": "SUP_XOUT_R", "2": "GND", "4": "GND"})
    c.R("1k", "SUP_XOUT", "SUP_XOUT_R")
    c.C("15p", "SUP_XIN")
    c.C("15p", "SUP_XOUT_R")
    # Flash.
    f = c.part("Memory_Flash:W25Q16JVSS", "W25Q16JVSSIQ", MPN="W25Q16JVSSIQ")
    f.connect(**{"1": "SUP_QSPI_SS", "2": "SUP_QSPI_SD1", "3": "SUP_QSPI_SD2", "4": "GND",
                 "5": "SUP_QSPI_SD0", "6": "SUP_QSPI_SCLK", "7": "SUP_QSPI_SD3", "8": SUP})
    c.C("100n", SUP)
    # BOOTSEL and RUN buttons.
    c.R("1k", "SUP_QSPI_SS", "SUP_BOOTSEL")
    c.part("Switch:SW_Push", "BOOTSEL", prefix="SW", footprint="Button_Switch_SMD:SW_SPST_PTS810",
           MPN="PTS810SJM250SMTRLFS").connect(**{"1": "SUP_BOOTSEL", "2": "GND"})
    c.R("10k", "SUP_RUN", SUP)
    c.part("Switch:SW_Push", "RESET", prefix="SW", footprint="Button_Switch_SMD:SW_SPST_PTS810",
           MPN="PTS810SJM250SMTRLFS").connect(**{"1": "SUP_RUN", "2": "GND"})
    # USB to hub port 1.
    c.R("27", "SUP_USB_DP_R", "HUB_DN1_P")
    c.R("27", "SUP_USB_DM_R", "HUB_DN1_N")
    # SWD: Raspberry Pi 3-pin debug connector (JST-SH, pin 1 SWCLK, 2 GND, 3 SWDIO), so the Debug
    # Probe's latching JST cable stays plugged in for remote recovery of the supervisor.
    c.part("Connector_Generic_MountingPin:Conn_01x03_MountingPin", "SWD", prefix="J",
           footprint="Connector_JST:JST_SH_SM03B-SRSS-TB_1x03-1MP_P1.00mm_Horizontal",
           MPN="SM03B-SRSS-TB(LF)(SN)").connect(
        **{"1": "SUP_SWCLK", "2": "GND", "3": "SUP_SWDIO", "MP": "GND"})
    c.R("2.2k", "SUP_SDA", SUP)
    c.R("2.2k", "SUP_SCL", SUP)
    c.R("10k", "SUP_nINT", SUP)
    c.R("1k", "SUP_LED", "SUP_LED_A")
    c.part("Device:LED", "green", prefix="D", footprint="LED_SMD:LED_0603_1608Metric").connect(
        A="SUP_LED_A", K="GND")
    hdr = c.part("Connector_Generic:Conn_02x04_Odd_Even", "SUP_AUX", prefix="J",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_2x04_P2.54mm_Vertical")
    for pin, net in zip(range(1, 9), ["DBG_A", "DBG_B", "SUP_AUX46", "SUP_AUX47", SUP, "SUP_5V", "GND", "GND"]):
        hdr[str(pin)] = net


def lsf(c, ref_a, pairs, ref_b_net):
    """One LSF0108 with its RefB bias (200 kΩ to 5 V) and an off switch on EN/RefB."""
    u = c.part("Logic_LevelTranslator:LSF0108PW", "LSF0108PWR", MPN="LSF0108PWR")
    u.connect(RefA=ref_a, RefB=ref_b_net, EN=ref_b_net, GND="GND")
    for i in range(8):
        a, b = pairs[i] if i < len(pairs) else (None, None)  # unused channels stay open
        u[f"A{i + 1}"], u[f"B{i + 1}"] = a, b
    c.R("200k", ref_b_net, "SUP_5V")
    c.C("100n", ref_b_net)
    c.C("100n", ref_a)
    q = c.part("Transistor_FET:2N7002", "2N7002", prefix="Q", MPN="2N7002")
    q.connect(G="LSF_OFF", D=ref_b_net, S="GND")
    return u


def gpio_path(c):
    c.start_block("GPIO path", 400)
    cm5 = ["ID_SD", "ID_SC"] + [f"GPIO{i}" for i in range(2, 28)]
    pairs = [(cm5[i], f"SUP_GPIO{i}") for i in range(28)]
    for k in range(4):
        lsf(c, "GPIO_VREF", pairs[k * 8:(k + 1) * 8], f"LSF_REFB{k + 1}")
    # Supervisor-side pull-ups: the LSF passes a high from the CM5 only up to GPIO_VREF.
    for k in range(7):
        rp = c.part("Device:R_Pack04", "4.7k", prefix="RN", footprint="Resistor_SMD:R_Array_Convex_4x0402",
                    MPN="CAY16-4701F4LF")
        for j in range(4):
            rp[f"R{j + 1}.1"] = f"SUP_GPIO{k * 4 + j}"
            rp[f"R{j + 1}.2"] = SUP
    misc1 = [("LED_nACT", "SUP_LED_nACT"), ("LED_nPWR", "SUP_LED_nPWR"), ("FAN_PWM", "SUP_FAN_PWM"),
             ("FAN_TACHO", "SUP_FAN_TACHO"), ("SYNC_OUT", "SUP_SYNC_OUT"), ("ETH_nLED2", "SUP_ETH_nLED2"),
             ("CAM_GPIO0", "SUP_CAM_GPIO0"), ("CAM_GPIO1", "SUP_CAM_GPIO1")]
    lsf(c, "+3V3_CM5", misc1, "LSF_REFB5")
    misc2 = [("SD_PWR_ON", "S2_SD_PWR_ON"), ("ETH_nLED3", "S2_ETH_nLED3"), ("PCIE_nWAKE", "S2_PCIE_nWAKE"),
             ("PCIE_nRST", "S2_PCIE_nRST"), ("VBUS_EN", "S2_VBUS_EN"), ("PCIE_PWR_EN", "S2_PCIE_PWR_EN")]
    lsf(c, "+3V3_CM5", misc2, "LSF_REFB6")
    for _, net in misc2:
        c.R("10k", net, SUP)
    # LSF_OFF is low (translators on) only when the supervisor asks AND the CM5's 3.3 V is up.
    c.R("10k", "LSF_OFF", SUP)
    qa = c.part("Transistor_FET:2N7002", "2N7002", prefix="Q", MPN="2N7002")
    qa.connect(G="LSF_ON_REQ", D="LSF_OFF", S="LSF_SERIES")
    qb = c.part("Transistor_FET:2N7002", "2N7002", prefix="Q", MPN="2N7002")
    qb.connect(G="+3V3_CM5", D="LSF_SERIES", S="GND")
    c.R("100k", "LSF_ON_REQ", "GND")


def control(c):
    c.start_block("Control", 500)
    # Expander 1 (0x20): open-drain controls (P00..P13), power and path enables (P14..P17).
    e1 = c.part("Interface_Expansion:TCA9555PWR", "TCA9555PWR", MPN="TCA9555PWR")
    e1.connect(VCC=SUP, GND="GND", SCL="SUP_SCL", SDA="SUP_SDA", A0="GND", A1="GND", A2="GND")
    e1["~{INT}"] = "SUP_nINT"
    od = ["PMIC_EN", "PWR_BUT", "nRPIBOOT", "EEPROM_nWP", "WL_nDISABLE", "BT_nDISABLE", "USB_OTG_ID",
          "VBUS_EN_SW", "PCIE_PWR_EN_SW", "VBAT_ON", "VREF_PR1", "ETH_SEL"]
    ports = [f"P0{i}" for i in range(8)] + [f"P1{i}" for i in range(8)]
    for port, net in zip(ports, od):
        e1[port] = f"OD_{net}"
        c.R("10k", f"OD_{net}", SUP)  # released by default
    for port, net in zip(ports[12:], ["EFUSE_EN", "DISCHARGE", "LSF_ON_REQ", "USB_MUX_SEL"]):
        e1[port] = net
    c.C("100n", SUP)
    # Open-drain buffers (74LVC3G07: Ioff, outputs tolerate 5.5 V for PWR_BUT).
    for k in range(4):
        b = c.part("74xGxx:74LVC3G07", "74LVC3G07DCUR", footprint="Package_SO:VSSOP-8_2.3x2mm_P0.5mm",
                   MPN="SN74LVC3G07DCUR")
        b["8"], b["4"] = SUP, "GND"
        for (i, o), net in zip((("1", "7"), ("3", "5"), ("6", "2")), od[k * 3:(k + 1) * 3]):
            b[i], b[o] = f"OD_{net}", net
        c.C("100n", SUP)
    # Expander 2 (0x21): ESP32, USB mux, HDMI; inputs from the CM5 status pins (via misc2 LSF).
    e2 = c.part("Interface_Expansion:TCA9555PWR", "TCA9555PWR", MPN="TCA9555PWR")
    e2.connect(VCC=SUP, GND="GND", SCL="SUP_SCL", SDA="SUP_SDA", A0=SUP, A1="GND", A2="GND")
    e2["~{INT}"] = "SUP_nINT"
    e2_nets = ["USB_MUX_nOE", "ESP_EN", "ESP_IO0", "HDMI0_HPD_EMU", "EFUSE_nFLT", "EFUSE_AUXOFF", "PD_ATTACH",
               "DDC_EN", "S2_SD_PWR_ON", "S2_ETH_nLED3", "S2_PCIE_nWAKE", "S2_PCIE_nRST", "S2_VBUS_EN",
               "S2_PCIE_PWR_EN", "USER_BTN", "SUP_LED2"]
    for port, net in zip(ports, e2_nets):
        e2[port] = net
    c.R("100k", "USB_MUX_nOE", SUP)
    c.R("100k", "HDMI0_HPD_EMU", "GND")
    c.R("100k", "DDC_EN", "GND")
    c.R("10k", "USER_BTN", SUP)
    c.part("Switch:SW_Push", "USER", prefix="SW", footprint="Button_Switch_SMD:SW_SPST_PTS810",
           MPN="PTS810SJM250SMTRLFS").connect(**{"1": "USER_BTN", "2": "GND"})
    c.R("1k", "SUP_LED2", "SUP_LED2_A")
    c.part("Device:LED", "yellow", prefix="D", footprint="LED_SMD:LED_0603_1608Metric").connect(
        A="SUP_LED2_A", K="GND")
    c.C("100n", SUP)
    # HDMI0 hot plug for monitor emulation: a 1 kΩ pull-up to the CM5's 5 V, switched by a PMOS.
    qn = c.part("Transistor_FET:2N7002", "2N7002", prefix="Q", MPN="2N7002")
    qn.connect(G="HDMI0_HPD_EMU", D="HPD_PGATE", S="GND")
    c.R("100k", "HPD_PGATE", "+5V_CM5")
    qp = c.part("Transistor_FET:AO3401A", "AO3401A", prefix="Q", MPN="AO3401A")
    qp.connect(G="HPD_PGATE", S="+5V_CM5", D="HPD_EMU_R")
    c.R("1k", "HPD_EMU_R", "HDMI0_HOTPLUG")
    # HDMI0 DDC to the supervisor (EDID target): TCA9517, B side at the CM5's 5 V, off with it.
    t = c.part("Logic_LevelTranslator:TCA9517D", "TCA9517DR", MPN="TCA9517DR")
    t.connect(VCCA=SUP, VCCB="+5V_CM5", SDAA="DDC_SUP_SDA", SCLA="DDC_SUP_SCL", SDAB="HDMI0_SDA",
              SCLB="HDMI0_SCL", EN="DDC_EN", GND="GND")
    c.R("4.7k", "DDC_SUP_SDA", SUP)
    c.R("4.7k", "DDC_SUP_SCL", SUP)
    c.C("100n", SUP)
    c.C("100n", "+5V_CM5")
    # Ethernet disconnect: CM5 pairs on A, MagJack on C (SEL high, pulled up by the CM5's 3.3 V).
    s = c.part("Analog_Switch:TS3L501ERUA", "TS3L501ERUAR", MPN="TS3L501ERUAR", Near="152,128")
    s.connect(VDD="+3V3_CM5", GND="GND", SEL="ETH_SEL", PD="GND")
    c.R("10k", "ETH_SEL", "+3V3_CM5", Near="152,128")
    for i in range(4):
        for pol in ("P", "N"):
            ch = 2 * i + (0 if pol == "P" else 1)
            s[f"A{ch}"] = f"TRD{i}_{pol}"
            s[f"C{ch}"] = f"MJ_TRD{i}_{pol}"
    for _ in range(2):
        c.C("100n", "+3V3_CM5", Near="152,128")


def usb(c):
    c.start_block("USB", 300)
    # Lab USB-C: upstream of the hub; Rd 5.1 kΩ on CC; ESD on D+/D-.
    j = c.part("Connector:USB_C_Receptacle_USB2.0_16P", "LAB", prefix="J",
               footprint="Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
               MPN="USB4105-GF-A")
    j.connect(VBUS="VBUS_LAB", GND="GND", SHIELD="GND", CC1="LAB_CC1", CC2="LAB_CC2", **{"D+": "LAB_DP", "D-": "LAB_DN"})
    c.R("5.1k", "LAB_CC1", "GND")
    c.R("5.1k", "LAB_CC2", "GND")
    esd = c.part("Power_Protection:USBLC6-2SC6", "USBLC6-2SC6", MPN="USBLC6-2SC6")
    esd.connect(**{"I/O1": "LAB_DP", "I/O2": "LAB_DN", "VBUS": "VBUS_LAB", "GND": "GND"})
    c.C("10u", "VBUS_LAB", size="0805")
    # Hub: USB2514B, default strap configuration.
    h = c.part("Interface_USB:USB2514B_Bi", "USB2514B/M2", MPN="USB2514B-AEZC-TR", Manufacturer="Microchip")
    h.connect(USBDP_UP="LAB_DP", USBDM_UP="LAB_DN", USBDP_DN1="HUB_DN1_P", USBDM_DN1="HUB_DN1_N",
              USBDP_DN2="HUB_DN2_P", USBDM_DN2="HUB_DN2_N", USBDP_DN3="HUB_DN3_P", USBDM_DN3="HUB_DN3_N",
              USBDP_DN4="HUB_DN4_P", USBDM_DN4="HUB_DN4_N", VDD33=SUP, VDDA33=SUP, VSS="GND", TEST="GND",
              CRFILT="HUB_CRFILT", PLLFILT="HUB_PLLFILT", RBIAS="HUB_RBIAS", XTALIN="HUB_XI", XTALOUT="HUB_XO",
              RESET_N="HUB_RESET_N", VBUS_DET="HUB_VBUS_DET", SDA="HUB_NON_REM1", SCL="HUB_CFG_SEL0",
              HS_IND="HUB_CFG_SEL1", SUSP_IND="HUB_NON_REM0", PRTPWR1="HUB_PRTPWR1", PRTPWR2="HUB_PRTPWR2",
              PRTPWR3="HUB_PRTPWR3", PRTPWR4="HUB_PRTPWR4", OCS_N1="HUB_OCS1", OCS_N2="HUB_OCS2",
              OCS_N3="HUB_OCS3", OCS_N4="HUB_OCS4")
    for net in ("HUB_NON_REM1", "HUB_CFG_SEL0", "HUB_CFG_SEL1", "HUB_NON_REM0", "HUB_PRTPWR1", "HUB_PRTPWR2"):
        c.R("10k", net, "GND")
    for net in ("HUB_OCS1", "HUB_OCS2", "HUB_OCS3", "HUB_OCS4"):
        c.R("10k", net, SUP)
    c.C("1u", "HUB_CRFILT")
    c.C("1u", "HUB_PLLFILT")
    c.R("12k", "HUB_RBIAS", "GND", MPN="RC0402FR-0712KL")
    c.R("10k", "HUB_RESET_N", SUP)
    c.C("100n", "HUB_RESET_N")
    c.R("10k", "VBUS_LAB", "HUB_VBUS_DET")
    c.R("18k", "HUB_VBUS_DET", "GND")
    for _ in range(6):
        c.C("100n", SUP)
    c.C("4.7u", SUP, size="0603")
    y = c.part("Device:Crystal_GND24", "24MHz", prefix="Y", footprint="Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm",
               MPN="ABM8-24.000MHZ-B2-T")
    y.connect(**{"1": "HUB_XI", "3": "HUB_XO", "2": "GND", "4": "GND"})
    c.C("18p", "HUB_XI")
    c.C("18p", "HUB_XO")
    c.R("1M", "HUB_XI", "HUB_XO")
    # Ports 3 and 4: USB-A for the HDMI capture stick and one spare, powered from the main input.
    for port in (3, 4):
        sw = c.part("Power_Management:AP22804AW5", "AP22804AW5-7", MPN="AP22804AW5-7")
        sw.connect(IN="VBUS_IN", EN=f"HUB_PRTPWR{port}", OUT=f"USBA{port}_VBUS", GND="GND")
        sw["~{FLG}"] = f"HUB_OCS{port}"
        c.R("10k", f"HUB_PRTPWR{port}", "GND")
        c.C("1u", "VBUS_IN")
        c.C("22u", f"USBA{port}_VBUS", size="0805")
        a = c.part("Connector:USB_A", "USB_A", prefix="J", footprint="Connector_USB:USB_A_Molex_67643_Horizontal",
                   MPN="67643-0910")
        a.connect(VBUS=f"USBA{port}_VBUS", GND="GND", Shield="GND",
                  **{"D+": f"HUB_DN{port}_P", "D-": f"HUB_DN{port}_N"})
    # CM5 USB 2.0 port: to the hub (rpiboot, gadget) or to the ESP32-S3 (CM5 as host).
    m = c.part("Interface_USB:FSUSB42MUX", "FSUSB42MUX", MPN="FSUSB42MUX")
    m.connect(VCC=SUP, GND="GND", SEL="USB_MUX_SEL", **{"D+": "USB2_P", "D-": "USB2_N", "HSD1+": "HUB_DN2_P",
              "HSD1-": "HUB_DN2_N", "HSD2+": "ESP_USB_P", "HSD2-": "ESP_USB_N", "~{OE}": "USB_MUX_nOE"})
    c.C("100n", SUP)
    c.R("100k", "USB_MUX_SEL", "GND")


def esp32(c):
    c.start_block("ESP32", 600)
    u = c.part("RF_Module:ESP32-S3-WROOM-1", "ESP32-S3-WROOM-1-N8", MPN="ESP32-S3-WROOM-1-N8",
               Manufacturer="Espressif")
    u.connect(GND="GND", EN="ESP_EN", IO0="ESP_IO0", TXD0="ESP_TXD", RXD0="ESP_RXD",
              **{"3V3": "ESP_3V3", "USB_D-": "ESP_USB_N", "USB_D+": "ESP_USB_P"})
    c.R("10k", "ESP_EN", "ESP_3V3")
    c.C("1u", "ESP_EN")
    c.R("10k", "ESP_IO0", "ESP_3V3")
    c.C("10u", "ESP_3V3", size="0805")
    c.C("100n", "ESP_3V3")


def headers(c, module):
    c.start_block("Headers", 700)
    ctl = ["PMIC_EN", "PWR_BUT", "nRPIBOOT", "EEPROM_nWP", "WL_nDISABLE", "BT_nDISABLE", "USB_OTG_ID",
           "LED_nACT", "LED_nPWR", "ETH_nLED2", "ETH_nLED3", "SYNC_OUT", "FAN_PWM", "FAN_TACHO", "VBUS_EN",
           "PCIE_PWR_EN", "PCIE_nWAKE", "PCIE_nRST", "SD_PWR_ON", "CAM_GPIO0", "CAM_GPIO1", "SDA0", "SCL0",
           "HDMI0_SDA", "HDMI0_SCL", "HDMI0_CEC", "HDMI0_HOTPLUG", "HDMI1_SDA", "HDMI1_SCL", "HDMI1_CEC",
           "HDMI1_HOTPLUG", "CM5_CC1", "CM5_CC2", "SD_VDD_OVERRIDE", "VBAT", "GPIO_VREF", "GND", "GND", "GND", "GND"]
    module["94"], module["96"], module["73"] = "CM5_CC1", "CM5_CC2", "SD_VDD_OVERRIDE"
    j = c.part("Connector_Generic:Conn_02x20_Odd_Even", "CM5_CTL", prefix="J",
               footprint="Connector_PinHeader_2.54mm:PinHeader_2x20_P2.54mm_Vertical")
    for i, net in enumerate(ctl):
        j[str(i + 1)] = net
    for pin, name in (("64", "SD_DAT5"), ("68", "SD_DAT4"), ("70", "SD_DAT7"), ("72", "SD_DAT6")):
        module[pin] = name
    sd = ["SD_CLK", "SD_CMD", "SD_DAT0", "SD_DAT1", "SD_DAT2", "SD_DAT3", "SD_DAT4", "SD_DAT5", "SD_DAT6",
          "SD_DAT7", "SD_PWR", "+5V_CM5", "+3V3_CM5", "+1V8_CM5", "GND", "GND", "GND", "GND", "GND", "GND"]
    j2 = c.part("Connector_Generic:Conn_02x10_Odd_Even", "CM5_SD_PWR", prefix="J",
                footprint="Connector_PinHeader_2.54mm:PinHeader_2x10_P2.54mm_Vertical")
    for i, net in enumerate(sd):
        j2[str(i + 1)] = net
    # M2.5 mounting holes in the extension, the same footprint as the IO Board's eight, so the
    # whole board takes one screw size (base plate and cover, docs/22 §4). Clear of the headers,
    # the USB-A ports and the ESP32, whose courtyard includes its antenna clearance (x > 314.9,
    # y > 122).
    for x, y in ((345.5, 84.0), (345.5, 112.0), (300.0, 84.0), (311.75, 156.5)):
        c.part("Mechanical:MountingHole", "MountingHole", prefix="H",
               footprint="CM5IO:MountingHole_2.7mm_M2.5_DIN965", Near=f"{x},{y},0")
    # Three global fiducials for assembly (the IO Board has none), not in a line. The assembler's
    # edge rails carry the tooling holes. Two go into free space near the IO Board's corners.
    for near in ("82.5,75", "84,146", "346.5,79.5,0"):
        c.part("Mechanical:Fiducial", "Fiducial", prefix="FID", footprint="Fiducial:Fiducial_1mm_Mask2mm",
               Near=near)
    # Nets driven from outside the schematic's view (connectors) or by passive parts.
    flagged = ["VBUS_IN", "VBUS_LAB", "VBAT_CELL", "SUP_5V", "VBUS_SHUNT", "SUP_VREG_AVDD", "+1V1_SUP",
               "M2_3v3"] + [f"LSF_REFB{k}" for k in range(1, 7)]
    for net in flagged:
        c.part("power:PWR_FLAG", "PWR_FLAG", prefix="#FLG", footprint="")["1"] = net


SHEETS = [
    ("IO Board", "ioboard.kicad_sch", "CM5 and the reused IO Board circuits"),
    ("Power", "power.kicad_sch", "Power input, eFuse, measurement, rails"),
    ("Supervisor", "supervisor.kicad_sch", "RP2350B supervisor"),
    ("USB", "usb.kicad_sch", "Lab USB hub, USB-A ports, CM5 USB mux"),
    ("GPIO path", "gpio_path.kicad_sch", "LSF0108 translators to the CM5"),
    ("Control", "control.kicad_sch", "Expanders, open-drain controls, HDMI and Ethernet"),
    ("ESP32", "esp32.kicad_sch", "ESP32-S3 USB device and radio partner"),
    ("Headers", "headers.kicad_sch", "Headers for every CM5 pin"),
]


def build():
    c = Circuit()
    module = ioboard(c)
    power(c)
    supervisor(c)
    usb(c)
    gpio_path(c)
    control(c)
    esp32(c)
    headers(c, module)
    # Reroute Ethernet through the disconnect switch: MagJack and ESD now sit on the C side.
    for ref in ("U1", "U2", "U3"):
        p = c.parts[ref]
        for num, net in list(p.nets.items()):
            if net.startswith("TRD"):
                p.nets[num] = "MJ_" + net
    return c


if __name__ == "__main__":
    c = build()
    print(len(c.parts), "parts,", len(c.nets()), "nets")
