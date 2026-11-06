// IMU Rate: shows the official Connect IQ sensor ceilings of this watch, then measures what
// registerSensorDataListener really delivers at that ceiling (samples/s and batch latency).
import Toybox.Application;
import Toybox.Graphics;
import Toybox.Lang;
import Toybox.Sensor;
import Toybox.System;
import Toybox.WatchUi;

class ImuRateApp extends Application.AppBase {
    function initialize() { AppBase.initialize(); }
    function getInitialView() { return [new ImuRateView()]; }
}

class ImuRateView extends WatchUi.View {
    var maxSys = -1; var maxA = -1; var maxG = -1; var maxM = -1;
    var nA = 0; var nG = 0; var batches = 0; var t0 = 0; var lastGap = 0; var lastT = 0;
    var err = "";

    function initialize() { View.initialize(); }

    function onShow() {
        if (Sensor has :getMaxSampleRate) { maxSys = Sensor.getMaxSampleRate(); }
        if (Sensor has :getMaxSampleRateForSensorType) {
            maxA = Sensor.getMaxSampleRateForSensorType(:accelerometer);
            maxG = Sensor.getMaxSampleRateForSensorType(:gyroscope);
            maxM = Sensor.getMaxSampleRateForSensorType(:magnetometer);
        }
        System.println("maxSys=" + maxSys + " acc=" + maxA + " gyro=" + maxG + " mag=" + maxM);
        var rate = maxA > 0 ? maxA : (maxSys > 0 ? maxSys : 25);
        var opts = { :period => 1, :accelerometer => { :enabled => true, :sampleRate => rate } };
        if (maxG > 0) { opts[:gyroscope] = { :enabled => true, :sampleRate => maxG }; }
        try {
            Sensor.registerSensorDataListener(method(:onData), opts);
            t0 = System.getTimer(); lastT = t0;
        } catch (e) {
            err = e.getErrorMessage();
        }
    }

    function onHide() { Sensor.unregisterSensorDataListener(); }

    function onData(d as Sensor.SensorData) as Void {
        var now = System.getTimer();
        if (d.accelerometerData != null) { nA += d.accelerometerData.x.size(); }
        if ((d has :gyroscopeData) && d.gyroscopeData != null) { nG += d.gyroscopeData.x.size(); }
        batches += 1; lastGap = now - lastT; lastT = now;
        WatchUi.requestUpdate();
    }

    function onUpdate(dc as Graphics.Dc) as Void {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();
        var s = (System.getTimer() - t0) / 1000.0;
        var lines = [
            "max sys " + maxSys + " Hz",
            "max acc " + maxA + "  gyro " + maxG,
            "max mag " + maxM,
            "recu acc " + (s > 0 ? (nA / s).format("%.1f") : "-") + " Hz",
            "recu gyro " + (s > 0 ? (nG / s).format("%.1f") : "-") + " Hz",
            "lots " + batches + "  ecart " + lastGap + " ms",
            err
        ];
        var y = dc.getHeight() / 8;
        var step = dc.getFontHeight(Graphics.FONT_XTINY) + 2;
        for (var i = 0; i < lines.size(); i++) {
            dc.drawText(dc.getWidth() / 2, y + i * step, Graphics.FONT_XTINY, lines[i], Graphics.TEXT_JUSTIFY_CENTER);
        }
    }
}
