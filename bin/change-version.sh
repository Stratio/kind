#!/bin/bash -e

BASEDIR=`dirname $0`/..

cd $BASEDIR

if [[ -z "$1" ]]; then
	VERSION=$(cat $BASEDIR/VERSION)
else
	VERSION=$1
fi

VERSION_GO_FILE="$BASEDIR/pkg/cmd/kind/version/version.go"
CORE_VERSION=$(echo "$VERSION" | sed -E "s/-.*//")

echo "Modifying cloud-provisioner version to: $1"
echo $VERSION > $BASEDIR/VERSION

sed -i "s/\(const versionCore = \"\)[^\"]*\"/\10.17.0-$CORE_VERSION\"/" "$VERSION_GO_FILE"
sed -i "s/\(const versionPreRelease = \"\)[^\"]*\"/\1SNAPSHOT\"/" "$VERSION_GO_FILE"
