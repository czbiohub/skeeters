##########
# Perform lca anlaysis on nt and nr hits
##########

import argparse, sys, time
parser = argparse.ArgumentParser()
parser.add_argument('--fpath', help="Path to file (local or on S3). For S3 files, the path should be in the form s3://bucket_name/path/to/file.")
parser.add_argument('--outpath', help="Path to output file (local or on S3).  For S3 files, the path should be in the form s3://bucket_name/path/to/file.")
parser.add_argument('--blast_type', help="Either 'nt' or 'nr'")
parser.add_argument('--filtered_blast_path', default=None, help="Path to filtered blast results file (local or on S3). For S3 files, the path should be in the form s3://bucket_name/path/to/file.")
parser.add_argument('--excluded_contigs_path', default=None, help="Path to the file containing excluded contigs and the reason for exclusion (local or on S3). For S3 files, the path should be in the form s3://bucket_name/path/to/file.")
parser.add_argument('--read_count_path', default=None, help="Path to read counts file (local or on S3). For S3 files, the path should be in the form s3://bucket_name/path/to/file.")
parser.add_argument('--ident_cutoff', default=0, help="Remove blast hits if their ident < ident_cutoff*max(ident) for a given contig.")
parser.add_argument('--align_len_cutoff', default=0, help="Remove blast hits if their alignment length < align_len_cutoff*max(align) for a given contig.")
parser.add_argument('--bitscore_cutoff', default=0, help="Remove blast hits if their bitscore < bitscore_cutoff*max(bitscore) for a given contig.")
parser.add_argument('--verbose', default="False", help="If true, print messages after each step of the script.")
args = parser.parse_args()

verbose = args.verbose=="True"

# import functions and variables from lca_functions.py
exec(open("lca_functions.py").read())

start_time = time.time()

# if contigs have 2 or fewer reads, then exclude them from LCA analysis
if (args.read_count_path.endswith(".json")):
    read_counts = load_json(args.read_count_path, colnames=["query", "read_count"])
else:
    read_counts = pd.read_csv(args.read_count_path, sep="\t", header=0).rename(columns={"contig_name":"query"})

filtered_contigs_by_read_count = read_counts[read_counts["read_count"]>2]

print_to_stdout("Read counts have been loaded: "+args.read_count_path, start_time, verbose)

if (len(filtered_contigs_by_read_count.index)==0):
    print_to_stdout(args.fpath+": no contig had more than 2 reads.", start_time, verbose)
    exit()

col_names = ["query", "subject", "identity", "align_length", "mismatches", 
        "gaps", "qstart", "qend", "sstart", "send", "evalue", "bitscore", "taxid", 
        "sci_name", "common_name", "subject_title", "qcov", "hsp_count"]

if args.blast_type =="nt":
    db = "nucleotide"
elif args.blast_type=="nr":
    db = "protein"
    
# obtain a single HSP for each query-subject pairing and read in blast results as a pandas data frame
blast_results = get_single_hsp(args.fpath, args.blast_type, col_names) 
if "qlen" not in blast_results:
    if ("~" in blast_results["query"].iloc[0]):
        blast_results = blast_results.assign(qlen=blast_results["query"].str.split("~").apply(lambda x: int(x[1].split("_")[3])))
    else:
        blast_results = blast_results.assign(qlen=blast_results["query"].str.split("_").apply(lambda x: int(x[3])))
print_to_stdout("Loaded blast file: "+args.fpath, start_time, verbose)


# data frame: whether or not each contig was included or excluded from blast analysis, and reason for exclusion
excluded_contigs = blast_results.groupby(["query"]).first().reset_index()[["query", "qlen"]].rename(columns={"qlen":"contig_length"})
if ("~" in excluded_contigs["query"].iloc[0]):
    excluded_contigs = excluded_contigs.assign(sample=excluded_contigs["query"].str.split("~").apply(lambda x: x[0]))
    excluded_contigs["query"] = excluded_contigs["query"].str.split("~").apply(lambda x: x[1])
    if "sample" in read_counts:
        selected_cols = ["sample", "query", "read_count"]
    else:
        selected_cols = ["query", "read_count"]
    excluded_contigs = pd.merge(excluded_contigs, read_counts[selected_cols], how="left").fillna(0)
    excluded_contigs["query"] = excluded_contigs[["sample", "query"]].apply(lambda x: x[0]+"~"+x[1], axis=1)
    queries = filtered_contigs_by_read_count.apply(lambda x: x["sample"]+"~"+x["query"], axis=1)
    excluded_contigs = excluded_contigs.assign(low_read_count=~excluded_contigs["query"].isin(queries))
else:
    excluded_contigs = excluded_contigs.assign(contig_length=excluded_contigs["query"].str.split("_").apply(lambda x: int(x[3])))
    excluded_contigs = pd.merge(excluded_contigs, read_counts, how="left", on="query").fillna(0)
    excluded_contigs = excluded_contigs.assign(low_read_count=~excluded_contigs["query"].isin(filtered_contigs_by_read_count["query"]))



# find missing taxids
blast_results["taxid"] = blast_results["taxid"].replace(to_replace=0, value=np.nan) # some synthetic constructs have taxid 0
if (blast_results["taxid"].isnull().any()):
    subjects_to_search = list(blast_results[blast_results["taxid"].isnull()]["subject"].unique())
    print_to_stdout(str(blast_results["taxid"].isnull().sum())+" blast hits corresponding to "+str(len(subjects_to_search))+" accession numbers have taxid 'NA'. Trying to find the taxid for these hits on NCBI.", start_time, verbose)
    subjects_taxids = [find_missing_taxid(x, db=db) for x in subjects_to_search]
    subjects_taxid_dict = dict(zip(subjects_to_search, subjects_taxids))
    blast_results.loc[blast_results["taxid"].isnull(), ["taxid"]] = blast_results[blast_results["taxid"].isnull()]["subject"].apply(lambda x: subjects_taxid_dict[x])
    blast_results = blast_results[~blast_results["taxid"].isnull()]

    
excluded_contigs = excluded_contigs.assign(taxid_na=~excluded_contigs["query"].isin(blast_results["query"]))
print_to_stdout(str(excluded_contigs["taxid_na"].sum())+" contigs were excluded because none of the subject taxids could be found.", start_time, verbose)        

if len(blast_results)==0:
    if (args.excluded_contigs_path.startswith("s3://")):
        df_to_s3(excluded_contigs, args.excluded_contigs_path)
    else:
        excluded_contigs.to_csv(args.excluded_contigs_path, sep="\t", index=False)
    exit()

# exclude contigs with hits to mosquito
all_hits_queries = list(blast_results["query"].unique())
print_to_stdout("remove contigs if they are likely hexapoda ", start_time, verbose)
hexapoda_hits = ncbi.get_descendant_taxa(ncbi.get_name_translator(["Hexapoda"])["Hexapoda"][0])
hexapoda_queries = blast_results[blast_results["taxid"].isin(hexapoda_hits)]["query"].unique().tolist()
before = blast_results[blast_results["query"].isin(hexapoda_queries)]
after = before.groupby(["query"], as_index=False).apply(filter_by_taxid, db=db, taxid=ncbi_older_db(["Hexapoda"], "get_name_translator")["Hexapoda"][0])
if (len(after)==0):
    hexa_contigs = before["query"].unique()
else:
    hexa_contigs = before[~before["query"].isin(after["query"])]["query"].unique()
blast_results = blast_results[~blast_results["query"].isin(hexa_contigs)]
excluded_contigs = excluded_contigs.assign(hexapoda=excluded_contigs["query"].isin(hexa_contigs))
print_to_stdout(str(len(hexa_contigs))+" contigs were likely hexapoda.", start_time, verbose)

if (len(blast_results)==0):
    if (args.excluded_contigs_path.startswith("s3://")):
        df_to_s3(excluded_contigs, args.excluded_contigs_path)
    else:
        excluded_contigs.to_csv(args.excluded_contigs_path, sep="\t", index=False)
    exit()

if (len(hexa_contigs)>0):
    print_to_stdout(str(len(hexa_contigs))+" contigs were likely hexapoda.", start_time, verbose)

# remove contigs with too few reads from blast_results
blast_results = blast_results[~blast_results["query"].isin(excluded_contigs["query"][excluded_contigs["low_read_count"]])]

print_to_stdout (str(len(blast_results["query"].unique())) + " out of " + str(len(excluded_contigs["query"])) + " contigs passed the filters and will be processed for LCA analysis.", start_time, verbose)

# fill in qcov information if any is missing
if (blast_results["qcov"].isnull().any()):
    missing_qcov = blast_results.loc[blast_results["qcov"].isnull(), :]
    blast_results.loc[blast_results["qcov"].isnull(), "qcov"] = missing_qcov["align_length"]/missing_qcov["qlen"]

# lca analysis
filtered_blast_results = blast_results.groupby(["query"], as_index=False).apply(
    select_taxids_for_lca, db=db,
    return_taxid_only=False
)

print_to_stdout("BLAST results have been filtered: "+args.filtered_blast_path, start_time, verbose)

lca_results = filtered_blast_results.groupby(["query"]).apply(get_lca)
additional_hexa_contigs = lca_results["query"][lca_results["taxid"].apply(lambda x: ncbi.get_name_translator(["Hexapoda"])["Hexapoda"][0] in ncbi_older_db(x, "get_lineage"))]
excluded_contigs.loc[excluded_contigs["query"].isin(additional_hexa_contigs), "hexapoda"] = True
filtered_blast_results = filtered_blast_results[~filtered_blast_results["query"].isin(additional_hexa_contigs)]
lca_results = lca_results[~lca_results["query"].isin(additional_hexa_contigs)]


# write results to file

if (args.filtered_blast_path is not None):
    if (args.filtered_blast_path.startswith("s3://")):
        df_to_s3(filtered_blast_results, args.filtered_blast_path)
    else:
        filtered_blast_results.to_csv(args.filtered_blast_path, sep="\t", index=False, header=True)
    print_to_stdout("BLAST results have been saved to : "+args.filtered_blast_path, start_time, verbose)

if (args.outpath.startswith("s3://")):
    df_to_s3(lca_results, args.outpath)
else:
    lca_results.to_csv(args.outpath, sep="\t", index=False)

if (args.excluded_contigs_path.startswith("s3://")):
    df_to_s3(excluded_contigs, args.excluded_contigs_path)
else:
    excluded_contigs.to_csv(args.excluded_contigs_path, sep="\t", index=False)
    
    
print_to_stdout("LCA analysis saved to file: "+args.outpath, start_time, verbose)
 

