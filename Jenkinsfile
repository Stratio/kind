@Library('libpipelines') _

hose {
    EMAIL = 'eos'
    BUILDTOOL = 'make'
    DEVTIMEOUT = 30
    //BUILDTOOL_IMAGE = 'qa.int.stratio.com:8443/stratio/keos-builder:0.3.1-PR29-SNAPSHOT'
    BUILDTOOL_IMAGE = 'golang:1.16'
    ANCHORE_POLICY = "production"
    VERSIONING_TYPE = 'stratioVersion-3-3'
    UPSTREAM_VERSION = '0.17.0'
    DEPLOYONPRS = true
    GRYPE_TEST = false

    DEV = { config ->
        doDeploy(conf:config, parameters: "GOCACHE=/tmp")
    }
}
