#!/bin/bash

# Check if the necessary arguments are provided
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <directory_with_files>"
    exit 1
fi

# Directory with the files
directory="$1"

# Check if the directory exists
if [ ! -d "$directory" ]; then
    echo "The directory '$directory' does not exist."
    exit 1
fi

# File that contains the name of the new registry
registry_file="$directory/REGISTRY"

# Check if the registry file exists
if [ ! -f "$registry_file" ]; then
    echo "The registry file '$registry_file' does not exist in the specified directory."
    exit 1
fi

# Read the first line that does not start with '#' from the file
new_registry=$(grep -v '^#' "$registry_file" | grep -m 1 .)

# Process all files starting with "images-"
for images_file in "$directory/imagenes-"*; do
    # Check if the file is readable
    if [ -r "$images_file" ]; then
        echo "Processing file: $images_file with new registry: $new_registry"
        
        # Read the file of image lists line by line
        while IFS= read -r line
        do
            # Skip empty lines
            if [ -z "$line" ]; then
                continue
            fi
            
            line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
            echo "Downloading the image: $line"
            docker pull "$line"

            # Split the line by "/" and replace only the first element with the new registry
            IFS='/' read -ra parts <<< "$line"
            parts[0]="$new_registry"
            new_image="$(IFS=/ ; echo "${parts[*]}")"

            # Rename the image
            echo "Renaming the image: $new_image"
            docker tag "$line" "$new_image"

            repository_flag_found=false
            ecr_repo="$(echo $new_image | cut -d "/" -f2- | rev | cut -d ":" -f2- | rev)"
            echo "ecr_repo value $ecr_repo"
            REPO_LIST=$(aws ecr describe-repositories --query "repositories[].repositoryName" --output text --region eu-west-1);
            echo "ok repos"
            for repo in $REPO_LIST; do
                if [ $ecr_repo = $repo ]; then
                    echo "The repository $repo already exists"
                    repository_flag_found=true
                    break
                fi
            done

            if [[ "$repository_flag_found" = false ]]; then
                echo "Creating repository $ecr_repo"
                aws ecr create-repository --repository-name $ecr_repo 
            fi

            # Push the image to the new registry
            echo "Pushing the image to the new registry: $new_image"
            docker push "$new_image"

        done < "$images_file"
        
        echo "Process completed for $images_file"
    else
        echo "The file $images_file is not readable. Skipping file."
    fi
done
